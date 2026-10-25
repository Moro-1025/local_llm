"""unsloth で Qwen3 に QLoRA を行う(Phase 5 / LORA-003)。WSL の venv で実行する。

    cd training && source ~/venvs/local_llm-training/bin/activate
    python train_lora.py                       # config.yaml の設定で学習
    python train_lora.py --config config.yaml --max-steps 5    # 動作確認(5 step だけ)

出力: runs/<日時>/
    config.yaml        使った設定のコピー
    log_history.json   step ごとの loss / eval_loss(trainer.state.log_history そのまま)
    summary.json       最初と最後の eval loss、学習時間、VRAM のピーク、データ件数
    loss.md            journal に貼れる Markdown の表
    adapter/           LoRA アダプタ(git 管理外。export_gguf.sh がここを読む)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def estimate_vram_gb(cfg: dict, params_b: float) -> dict:
    """ざっくりした VRAM 見積もり(GiB)。学習前に 12 GB を超えないことを確認するため。
    4bit 重み ≒ params × 0.5 byte、LoRA(r=16, 全線形層) ≒ 数十 MB、
    活性は gradient checkpointing 前提で batch × seq × hidden × 層数 × 2 byte × 係数。"""
    weights = params_b * 1e9 * 0.5 / 2**30 + 0.6  # embedding/lm_head は 16bit で残るぶんを足す
    bs = cfg["train"]["per_device_batch_size"]
    seq = cfg["model"]["max_seq_length"]
    act = bs * seq * 2560 * 36 * 2 * 1.5 / 2**30 / 8  # Qwen3-4B: hidden 2560, 36 層。checkpointing で約 1/8
    lora_and_opt = 0.3
    total = weights + act + lora_and_opt + 1.0  # 1.0 は CUDA コンテキストや断片化の余裕
    return {"weights": round(weights, 2), "activations": round(act, 2), "lora_optimizer": lora_and_opt, "total": round(total, 2)}


def build_text(tokenizer, system_prompt: str, q: str, a: str, *, prepend_empty_think: bool) -> str:
    content = ("<think>\n\n</think>\n\n" + a) if prepend_empty_think else a
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": q},
        {"role": "assistant", "content": content},
    ]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--max-steps", type=int, default=0, help="動作確認用。0 なら epochs に従う")
    ap.add_argument("--run-name", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    run_dir = HERE / "runs" / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(args.config, run_dir / "config.yaml")

    params_b = 4.0 if "4B" in cfg["model"]["name"] else 1.7 if "1.7B" in cfg["model"]["name"] else 4.0
    est = estimate_vram_gb(cfg, params_b)
    print(f"VRAM 見積もり (GiB): {est}")
    if est["total"] > 11.0:
        print("見積もりが 11 GiB を超えるので中止(VRAM_GUARD)。batch か max_seq_length を下げる", file=sys.stderr)
        return 2

    import torch
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    free, total = torch.cuda.mem_get_info()
    print(f"GPU 空き {free/2**30:.2f} / {total/2**30:.2f} GiB(学習前)")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["model"]["name"],
        max_seq_length=cfg["model"]["max_seq_length"],
        load_in_4bit=cfg["model"]["load_in_4bit"],
        dtype=None,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=cfg["lora"]["r"],
        lora_alpha=cfg["lora"]["alpha"],
        lora_dropout=cfg["lora"]["dropout"],
        target_modules=cfg["lora"]["target_modules"],
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=cfg["train"]["seed"],
    )

    dcfg = cfg["data"]
    ds = load_dataset("json", data_files={"train": str(HERE / dcfg["train"]), "eval": str(HERE / dcfg["eval"])})

    def fmt(batch):
        return {
            "text": [
                build_text(tokenizer, dcfg["system_prompt"], q, a, prepend_empty_think=dcfg["prepend_empty_think"])
                for q, a in zip(batch["question"], batch["answer"])
            ]
        }

    ds = ds.map(fmt, batched=True, remove_columns=ds["train"].column_names)
    print("サンプル:\n" + ds["train"][0]["text"])

    tcfg = cfg["train"]
    sft = SFTConfig(
        output_dir=str(run_dir / "checkpoints"),
        per_device_train_batch_size=tcfg["per_device_batch_size"],
        per_device_eval_batch_size=tcfg["per_device_batch_size"],
        gradient_accumulation_steps=tcfg["gradient_accumulation_steps"],
        warmup_steps=tcfg["warmup_steps"],
        num_train_epochs=tcfg["epochs"],
        max_steps=args.max_steps if args.max_steps else -1,
        learning_rate=float(tcfg["learning_rate"]),
        weight_decay=tcfg["weight_decay"],
        lr_scheduler_type=tcfg["lr_scheduler_type"],
        optim=tcfg["optim"],
        seed=tcfg["seed"],
        logging_steps=tcfg["logging_steps"],
        eval_strategy="steps",
        eval_steps=tcfg["eval_steps"],
        eval_on_start=tcfg["eval_on_start"],
        save_strategy="no",
        report_to="none",
        max_length=cfg["model"]["max_seq_length"],
        dataset_text_field="text",
        packing=False,
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),
    )
    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=ds["train"], eval_dataset=ds["eval"], args=sft)
    if tcfg["train_on_responses_only"]:
        trainer = train_on_responses_only(
            trainer, instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n"
        )

    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    result = trainer.train()
    elapsed = time.time() - t0

    history = trainer.state.log_history
    (run_dir / "log_history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    evals = [h for h in history if "eval_loss" in h]
    trains = [h for h in history if "loss" in h and "eval_loss" not in h]
    summary = {
        "run": args.run_name,
        "model": cfg["model"]["name"],
        "train_rows": len(ds["train"]),
        "eval_rows": len(ds["eval"]),
        "steps": trainer.state.global_step,
        "epochs": tcfg["epochs"] if not args.max_steps else None,
        "elapsed_sec": round(elapsed, 1),
        "train_loss_first": trains[0]["loss"] if trains else None,
        "train_loss_last": trains[-1]["loss"] if trains else None,
        "eval_loss_first": evals[0]["eval_loss"] if evals else None,
        "eval_loss_last": evals[-1]["eval_loss"] if evals else None,
        "eval_loss_decreased": (evals[-1]["eval_loss"] < evals[0]["eval_loss"]) if len(evals) >= 2 else None,
        "vram_peak_allocated_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "vram_peak_reserved_gib": round(torch.cuda.max_memory_reserved() / 2**30, 2),
        "vram_estimate_gib": est,
        "train_runtime_metrics": result.metrics,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["| step | epoch | train loss | eval loss |", "| --- | --- | --- | --- |"]
    merged: dict[int, dict] = {}
    for h in history:
        merged.setdefault(h["step"], {"epoch": h.get("epoch")}).update(h)
    for step in sorted(merged):
        h = merged[step]
        tl = f"{h['loss']:.4f}" if "loss" in h else ""
        el = f"{h['eval_loss']:.4f}" if "eval_loss" in h else ""
        lines.append(f"| {step} | {h.get('epoch', 0):.2f} | {tl} | {el} |")
    (run_dir / "loss.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    model.save_pretrained(str(run_dir / "adapter"))
    tokenizer.save_pretrained(str(run_dir / "adapter"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"保存先: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
