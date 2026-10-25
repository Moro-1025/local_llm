"""契約の scope グロブ。agent/harness/src/glob.ts と同じ規則(** / * / ? 、リテラルは配下も含む)。"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_SPECIAL = set("\\^$+.()|{}[]")


def normalize_relative(path: str) -> str:
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = re.sub(r"/+", "/", p)
    if p.endswith("/") and len(p) > 1:
        p = p[:-1]
    return p


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    pat = normalize_relative(pattern)
    out = ""
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "/" and pat[i + 1 :] == "**":
            out += "(?:/.*)?"
            break
        if c == "*":
            if pat[i + 1 : i + 2] == "*":
                if pat[i + 2 : i + 3] == "/":
                    out += "(?:.*/)?"
                    i += 3
                    continue
                out += ".*"
                i += 2
                continue
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c in _SPECIAL:
            out += "\\" + c
        else:
            out += c
        i += 1
    if not re.search(r"[*?]", pat):
        out += "(?:/.*)?"
    return re.compile("^" + out + "$")


def matches_any(rel_path: str, patterns: list[str]) -> bool:
    p = normalize_relative(rel_path)
    return any(glob_to_regex(pat).match(p) for pat in patterns)


def relative_to_workspace(workspace: Path, path: str) -> str | None:
    """workspace からの相対パス(/ 区切り)。外に出るなら None。"""
    abs_path = (Path(workspace) / path).resolve()
    try:
        rel = abs_path.relative_to(Path(workspace).resolve())
    except ValueError:
        return None
    return str(PurePosixPath(*rel.parts)) if rel.parts else "."


def check_read_scope(workspace: Path, path: str, scope: dict) -> str | None:
    """読み取り可なら None、不可なら理由。"""
    rel = relative_to_workspace(workspace, path)
    if rel is None:
        return f"workspace の外: {path}"
    if matches_any(rel, scope.get("forbidden", [])):
        return f"scope.forbidden に一致: {rel}"
    if not matches_any(rel, scope.get("read", [])):
        return f"scope.read の外: {rel}"
    return None
