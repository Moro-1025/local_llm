"""eval/results/*.json を eval/schemas/qa-result.schema.json で検証し、採点モデル ≠ 回答モデルを文字列比較で確かめる(EVAL-003)。

使い方:
    uv run python scripts/agent/check_eval_results.py                 # eval/results/ の全 JSON
    uv run python scripts/agent/check_eval_results.py path/to/x.json  # 指定ファイルだけ

結果が 1 件も無いときは「対象なし」で終了コード 0(評価を一度も走らせていない環境でも verify.ps1 を通すため)。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "eval" / "schemas" / "qa-result.schema.json"
RESULTS_DIR = ROOT / "eval" / "results"


def check_result(path: Path, validator: Draft202012Validator) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = [
        f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    ]
    grader = (doc.get("grader") or {}).get("model")
    answer = (doc.get("answer") or {}).get("model")
    if isinstance(grader, str) and isinstance(answer, str) and grader.strip() == answer.strip():
        errors.append(f"grader.model と answer.model が同じです: {grader}")
    if doc.get("config", {}).get("answer_model") != answer:
        errors.append("config.answer_model と answer.model が一致しません")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    targets = [Path(a) for a in args] if args else sorted(RESULTS_DIR.glob("*.json"))
    targets = [p for p in targets if not p.name.endswith(".partial.json")]
    if not targets:
        print("eval/results に検証対象がありません(評価未実行)")
        return 0
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")), format_checker=FormatChecker())
    failed = 0
    for path in targets:
        errors = check_result(path, validator)
        rel = path.relative_to(ROOT) if path.is_absolute() and ROOT in path.parents else path
        if errors:
            failed += 1
            print(f"NG {rel}")
            for e in errors:
                print(f"   - {e}")
        else:
            print(f"OK {rel} (grader != answer)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
