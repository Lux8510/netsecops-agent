#!/usr/bin/env python3
"""Create an independent v0.6 source/demo record; never change older freezes."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from check_publication import check as check_publication


ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs/ospf-baseline"
MANIFEST = ROOT / "docs/v0.6-release-manifest.json"
PRIVATE_EXTRACT = LAB / "tests/fixtures/v04-failures/normal-acceptance.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_files() -> list[Path]:
    selected = [ROOT / name for name in (
        ".env.example", ".gitignore", "README.md", "agent-settings.json",
        "run_clean_offline_tests.py", "run_clean_offline_tests_v06.py",
        ".github/workflows/offline.yml", "docs/PROJECT_OVERVIEW.md",
        "docs/DEMO_SCRIPT.md", "docs/INTERVIEW_QA.md", "docs/VALIDATION_v0.6.md",
        "docs/v0.6-test-results.json",
        "docs/images/workbench-v06.png", "labs/ospf-baseline/topology.clab.yml",
        "tools/export_demo_examples.py", "tools/check_publication.py",
        "tools/create_v06_manifest.py", "web/package.json", "web/package-lock.json",
        "web/tsconfig.json", "web/vite.config.ts", "web/index.html",
    )]
    selected += list((ROOT / "examples").rglob("*"))
    selected += list((ROOT / "web/src").rglob("*"))
    selected += list(LAB.glob("*.py"))
    selected += list((LAB / "tests").glob("*.py"))
    selected += list((LAB / "tests/fixtures").rglob("*"))
    selected += list((LAB / "configs").rglob("frr.conf"))
    return sorted({path for path in selected if path.is_file() and path != PRIVATE_EXTRACT})


def main() -> int:
    if MANIFEST.exists():
        raise RuntimeError("v0.6 release manifest already exists; refuse overwrite")
    check_publication()
    offline = sorted((ROOT / "review-results").glob("v0.6-demo-offline-*/result.json"))
    candidates = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in offline]
    candidates = [(path, result) for path, result in candidates if
                  result.get("platform") == "linux" and result.get("tests_run") == 72 and
                  result.get("skipped") == 0 and result.get("status") == "PASS"]
    if not candidates:
        raise RuntimeError("Run all 72 v0.6 offline tests in a Linux/WSL clean copy first")
    offline_path, offline_result = candidates[-1]
    web_report_path = ROOT / "review-results/v0.6-web-tests.json"
    web_report = json.loads(web_report_path.read_text(encoding="utf-8"))
    if web_report.get("success") is not True or web_report.get("numPassedTests") != 7 or web_report.get("numFailedTests") != 0:
        raise RuntimeError("The 7 front-end tests have not all passed")
    if not (ROOT / "web/dist/index.html").is_file() or len(list((ROOT / "web/dist/cases").glob("*.json"))) != 5:
        raise RuntimeError("Production build with five examples is missing")
    historic = LAB / "reliability-v0.5.json"
    value = {
        "schema_version": 1,
        "version": "v0.6-demo",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "Offline source/demo packaging only; no new model or live network acceptance",
        "previous_freeze": {"path": historic.relative_to(ROOT).as_posix(), "sha256": sha(historic),
                            "relation": "Unmodified historical anchor, not a current-code verification"},
        "validation": {
            "python_clean_copy": {"tests": offline_result["tests_run"], "skipped": offline_result["skipped"],
                                  "platform": offline_result["platform"], "result_sha256": sha(offline_path)},
            "frontend": {"tests": web_report["numPassedTests"], "failed": web_report["numFailedTests"],
                         "report_sha256": sha(web_report_path), "typecheck": "PASS", "build": "PASS"},
            "browser_check": "PASS; details in docs/VALIDATION_v0.6.md",
            "github_actions": "NOT_RUN; changes intentionally not pushed",
        },
        "files_sha256": {path.relative_to(ROOT).as_posix(): sha(path) for path in selected_files()},
    }
    MANIFEST.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Version: {value['version']}; files: {len(value['files_sha256'])}; manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
