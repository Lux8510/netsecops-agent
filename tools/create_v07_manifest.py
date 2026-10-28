#!/usr/bin/env python3
"""Freeze this offline progress preview independently of earlier records."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from create_v06_manifest import ROOT, selected_files, sha
from check_publication import check as check_publication


MANIFEST = ROOT / "docs/v0.7-release-manifest.json"
PRIOR = ROOT / "docs/v0.6-release-manifest.json"


def main() -> int:
    if MANIFEST.exists():
        raise RuntimeError("v0.7 manifest already exists; refuse overwrite")
    offline = sorted((ROOT / "review-results").glob("v0.7-progress-offline-*/result.json"))
    valid = [(path, value) for path in offline
             if (value := json.loads(path.read_text(encoding="utf-8"))).get("status") == "PASS"
             and value.get("platform") == "linux" and value.get("tests_run") == 77
             and value.get("skipped") == 0]
    if not valid:
        raise RuntimeError("Run all 77 v0.7 tests from a clean WSL/Linux copy first")
    offline_path, _ = valid[-1]
    web_path = ROOT / "review-results/v0.7-web-tests.json"
    web = json.loads(web_path.read_text(encoding="utf-8"))
    if web.get("success") is not True or web.get("numPassedTests") != 7 or web.get("numFailedTests") != 0:
        raise RuntimeError("The seven frontend tests must pass")
    if not (ROOT / "web/dist/index.html").is_file() or len(list((ROOT / "web/dist/cases").glob("*.json"))) != 5:
        raise RuntimeError("The production build must contain five reviewed cases")
    if not PRIOR.is_file():
        raise RuntimeError("Independent v0.6 historical manifest is missing")
    extras = [ROOT / path for path in (
        "requirements-progress.txt", "run_clean_offline_tests_v07.py",
        "docs/VALIDATION_v0.7.md", "docs/v0.7-test-results.json",
        "docs/images/progress-v07.png", "tools/create_v07_manifest.py",
    )]
    files = sorted(set(selected_files() + extras))
    if not all(path.is_file() for path in files):
        raise RuntimeError("A v0.7 source or record file is missing")
    screenshot_hash = sha(ROOT / "docs/images/progress-v07.png")
    if screenshot_hash not in (ROOT / "docs/VALIDATION_v0.7.md").read_text(encoding="utf-8"):
        raise RuntimeError("The documented browser screenshot hash does not match")
    record = {
        "schema_version": 1,
        "version": "v0.7-progress",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "Optional Agent callbacks and local mock SSE; no real-model or live-network acceptance",
        "previous_manifest": {"path": PRIOR.relative_to(ROOT).as_posix(), "sha256": sha(PRIOR),
                              "relation": "Historical anchor, not a current-tree verification"},
        "validation": {
            "python_clean_copy": {"tests": 77, "skipped": 0, "result_sha256": sha(offline_path)},
            "frontend": {"tests": 7, "failed": 0, "typecheck": "PASS", "build": "PASS",
                         "report_sha256": sha(web_path)},
            "browser_mock_sse": "PASS; see docs/VALIDATION_v0.7.md",
            "real_model_sse": "NOT_RUN", "live_network": "NOT_RUN",
            "github_actions": "NOT_RUN; changes not pushed",
        },
        "files_sha256": {path.relative_to(ROOT).as_posix(): sha(path) for path in files},
    }
    MANIFEST.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    check_publication()
    print(f"Version: {record['version']}; files: {len(record['files_sha256'])}; manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
