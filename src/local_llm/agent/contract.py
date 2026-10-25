"""Task Contract の読み込み、contract_hash の計算、JSON Schema 検証。

hash の規則は scripts/agent/check_contracts.py と agent/harness/src/policy.ts と同じ:
contract_hash を null にした契約を、キー順ソート・区切り最小の JSON にして sha256。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_RELATIVE = Path("docs") / "harness" / "schemas"


def canonical_hash(contract: dict) -> str:
    body = dict(contract)
    body["contract_hash"] = None
    data = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass
class Contract:
    path: Path
    data: dict
    hash: str
    schema_errors: list[str] = field(default_factory=list)
    schema_checked: bool = False

    @property
    def hash_matches(self) -> bool:
        return self.data.get("contract_hash") == self.hash

    @property
    def criteria(self) -> list[dict]:
        return list(self.data.get("completion_criteria", []))

    @property
    def allowed_tools(self) -> list[str]:
        return list(self.data.get("capability_profile", {}).get("allowed_tools", []))

    @property
    def scope(self) -> dict:
        return self.data.get("scope", {"read": [], "write": [], "forbidden": []})

    @property
    def budget(self) -> dict:
        return dict(self.data.get("execution_budget", {}))


def validate_against_schema(doc: dict, schema_name: str, schema_dir: Path) -> tuple[bool, list[str]]:
    """(検証したか, エラー一覧)。jsonschema が無い、または Schema ファイルが無いときは (False, [])。"""
    schema_path = schema_dir / f"{schema_name}.schema.json"
    if not schema_path.exists():
        return False, []
    try:
        from jsonschema import Draft202012Validator
    except ImportError:  # pragma: no cover - dev 依存が無い環境
        return False, []
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        where = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{where}: {err.message}")
    return True, errors


def load_contract(path: Path, *, schema_dir: Path | None = None) -> Contract:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    contract = Contract(path=Path(path), data=data, hash=canonical_hash(data))
    if schema_dir is not None:
        checked, errors = validate_against_schema(data, "task-contract", schema_dir)
        contract.schema_checked = checked
        contract.schema_errors = errors
    return contract
