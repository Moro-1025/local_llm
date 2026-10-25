"""contract.py / scope.py: hash の規則が check_contracts.py と一致し、グロブが TS 側と同じ判定をする。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from local_llm.agent.contract import canonical_hash, load_contract
from local_llm.agent.scope import check_read_scope, glob_to_regex, matches_any, normalize_relative

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "agent" / "contracts" / "summarize-docs.json"
SCHEMA_DIR = ROOT / "docs" / "harness" / "schemas"


def test_sample_contract_is_valid_and_hash_matches():
    c = load_contract(SAMPLE, schema_dir=SCHEMA_DIR)
    assert c.schema_checked, "jsonschema で検証できること"
    assert c.schema_errors == []
    assert c.hash_matches, f"contract_hash を更新してください: {c.hash}"
    assert c.allowed_tools == ["rag_search", "read_file", "list_files", "finish"]


def test_canonical_hash_same_as_check_contracts():
    sys.path.insert(0, str(ROOT / "scripts" / "agent"))
    import check_contracts  # type: ignore

    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert canonical_hash(data) == check_contracts.canonical_hash(data)


def test_canonical_hash_matches_typescript_implementation():
    """agent/harness/src/policy.ts の contractHash と同じ値になる(node が無ければスキップ)。"""
    import shutil

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    script = (
        'import { contractHash } from "./src/policy.ts";'
        'import { readFileSync } from "node:fs";'
        f'const c = JSON.parse(readFileSync({json.dumps(str(SAMPLE))}, "utf8"));'
        "console.log(contractHash(c));"
    )
    tmp = ROOT / "agent" / "harness" / "_hash_check.mts"
    tmp.write_text(script, encoding="utf-8")
    try:
        res = subprocess.run([node, "--experimental-strip-types", "--no-warnings", str(tmp)], cwd=ROOT,
                             capture_output=True, text=True, timeout=60)
    finally:
        tmp.unlink(missing_ok=True)
    assert res.returncode == 0, res.stderr
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert res.stdout.strip() == canonical_hash(data)


def test_glob_rules_match_typescript_cases():
    assert normalize_relative(".\\docs\\a.md") == "docs/a.md"
    re_md = glob_to_regex("docs/**/*.md")
    assert re_md.match("docs/a.md") and re_md.match("docs/x/y/z.md")
    assert not re_md.match("docs/a.txt") and not re_md.match("src/a.md")
    assert glob_to_regex("docs/*.md").match("docs/a.md") and not glob_to_regex("docs/*.md").match("docs/sub/a.md")
    assert glob_to_regex("docs").match("docs") and glob_to_regex("docs").match("docs/a.md")
    assert not glob_to_regex("docs").match("docs2/a.md")
    assert glob_to_regex(".env").match(".env") and not glob_to_regex(".env").match(".envrc")
    assert glob_to_regex("docs/**").match("docs") and glob_to_regex("docs/**").match("docs/a/b.md")
    assert glob_to_regex("**/*.gguf").match("model.gguf") and glob_to_regex("**/*.gguf").match("a/b/model.gguf")
    assert matches_any("docs/x.md", ["README.md", "docs/**"]) and not matches_any("src/x.py", ["README.md", "docs/**"])


def test_check_read_scope(tmp_path: Path):
    scope = {"read": ["docs/**", "README.md"], "write": [], "forbidden": [".env", "**/*.gguf"]}
    assert check_read_scope(tmp_path, "docs/a.md", scope) is None
    assert check_read_scope(tmp_path, "README.md", scope) is None
    assert "外" in check_read_scope(tmp_path, "../x", scope)
    assert "scope.read" in check_read_scope(tmp_path, "src/a.py", scope)
    assert "forbidden" in check_read_scope(tmp_path, ".env", scope)
    assert "forbidden" in check_read_scope(tmp_path, "docs/m.gguf", scope)
