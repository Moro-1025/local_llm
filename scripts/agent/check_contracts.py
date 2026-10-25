"""docs/harness/contracts/*.json を ai_harness の JSON Schema で検証し、contract_hash を照合する。

使い方:
    uv run python scripts/agent/check_contracts.py            # 全件検証
    uv run python scripts/agent/check_contracts.py --rehash   # hash を再計算して書き戻す(内容を変えたとき)

contract_hash は「contract_hash を null にした契約を、キー順ソート・区切り最小の JSON にして sha256」。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "docs" / "harness" / "schemas"
CONTRACT_DIR = ROOT / "docs" / "harness" / "contracts"
REPORT_DIR = ROOT / "docs" / "harness" / "reports"


def canonical_hash(contract: dict) -> str:
    body = dict(contract)
    body["contract_hash"] = None
    data = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_schema(name: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def check_file(path: Path, validator: Draft202012Validator, *, rehash: bool, kind: str) -> list[str]:
    errors: list[str] = []
    doc = json.loads(path.read_text(encoding="utf-8"))

    if kind == "task-contract":
        expected = canonical_hash(doc)
        if doc.get("contract_hash") != expected:
            if rehash:
                doc["contract_hash"] = expected
                path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"  rehash: {path.name} -> {expected[:19]}...")
            else:
                errors.append(f"contract_hash が内容と一致しません(--rehash で更新): {doc.get('contract_hash')} != {expected}")

    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rehash", action="store_true", help="contract_hash を再計算して書き戻す")
    args = parser.parse_args(argv)

    targets: list[tuple[Path, str]] = []
    targets += [(p, "task-contract") for p in sorted(CONTRACT_DIR.glob("*.json"))]
    targets += [(p, "completion-decision") for p in sorted(REPORT_DIR.glob("*.decision.json"))]
    targets += [(p, "verification-result") for p in sorted(REPORT_DIR.glob("*.verification.json"))]
    if not targets:
        print("検証対象がありません")
        return 1

    validators = {kind: load_schema(kind) for kind in {k for _, k in targets}}
    failed = 0
    for path, kind in targets:
        errors = check_file(path, validators[kind], rehash=args.rehash, kind=kind)
        rel = path.relative_to(ROOT)
        if errors:
            failed += 1
            print(f"NG {rel}")
            for e in errors:
                print(f"   - {e}")
        else:
            print(f"OK {rel} ({kind})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
