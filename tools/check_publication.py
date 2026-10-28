#!/usr/bin/env python3
"""Check the deliberately small publishable evidence set and demo doc links."""

from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
FIXTURES = ROOT / "labs/ospf-baseline/tests/fixtures"
PRIVATE_EXTRACT = FIXTURES / "v04-failures/normal-acceptance.json"
DOCUMENTS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")),
             EXAMPLES / "README.md", FIXTURES / "README.md"]
SECRET_OR_PATH = re.compile(
    r"(?:[A-Za-z]:\\Users\\|/mnt/[a-z]/Users/|Authorization\s*[:=]|"
    r"Bearer\s+\S+|sk-[A-Za-z0-9]{10,})", re.IGNORECASE,
)
LINK = re.compile(r"\]\(([^)]+)\)")


def check() -> int:
    index = json.loads((EXAMPLES / "index.json").read_text(encoding="utf-8"))
    if index.get("schema_version") != 1 or len(index.get("cases", [])) != 5:
        raise ValueError("Expected exactly five versioned, indexed demo cases")
    files = [EXAMPLES / "index.json", *sorted((EXAMPLES / "cases").glob("*.json")),
             *sorted(path for path in FIXTURES.rglob("*") if path.is_file() and path != PRIVATE_EXTRACT)]
    for path in files:
        if SECRET_OR_PATH.search(path.read_text(encoding="utf-8")):
            raise ValueError(f"Potential credential or personal path in publishable file: {path.relative_to(ROOT)}")
    for item in index["cases"]:
        path = EXAMPLES / item["path"]
        if not path.is_file() or item["path"] != f"cases/{item['id']}.json":
            raise ValueError(f"Unlisted or missing example: {item['id']}")
        example = json.loads(path.read_text(encoding="utf-8"))
        if example.get("case_id") != item["id"] or example.get("trace", {}).get("mode") != item["run_mode"]:
            raise ValueError(f"Example metadata does not match the actual saved run: {item['id']}")
    if (ROOT / ".env.example").read_text(encoding="utf-8").strip() != "DEEPSEEK_API_KEY=":
        raise ValueError(".env.example must be an empty key placeholder only")
    for path in DOCUMENTS:
        text = path.read_text(encoding="utf-8")
        if SECRET_OR_PATH.search(text):
            raise ValueError(f"Personal path or credential-like text in current documentation: {path.relative_to(ROOT)}")
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if not (path.parent / relative).exists():
                raise ValueError(f"Broken local link in {path.relative_to(ROOT)}: {target}")
    print(f"Publication check: PASS; reviewed data files: {len(files)}; demo cases: {len(index['cases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
