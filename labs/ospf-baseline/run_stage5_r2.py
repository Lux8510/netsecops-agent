#!/usr/bin/env python3
"""Run three held-out r2 faults only after the Stage 5 logic is frozen."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

from collect_evidence import LAB_DIR, collect_evidence, ensure_wsl2_and_sudo, read_layout, utc_now
from run_area_mismatch import healthy, metrics_from_evidence
from run_fault_scenario import run_experiment
from reliability_freeze_v05 import verify


FAULTS = ("area_mismatch", "admin_down", "ospf_passive")


def main() -> int:
    freeze = verify()
    ensure_wsl2_and_sudo()
    root = LAB_DIR / "stage5-r2-tests" / ("batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                          + "-" + uuid.uuid4().hex[:8])
    root.mkdir(parents=True, exist_ok=False)
    record: dict = {"schema_version": 1, "started_at_utc": utc_now(),
                    "freeze_at_utc": freeze["frozen_at_utc"], "code_version": freeze["version"], "target": "r2",
                    "experiments": [], "final_network": "NOT_VERIFIED", "status": "IN_PROGRESS", "errors": []}
    path = root / "batch.json"
    try:
        for fault in FAULTS:
            verify()
            print(f"Running held-out case {len(record['experiments']) + 1}/3; fixed target r2", flush=True)
            experiment, outcome = run_experiment(fault, "r2", LAB_DIR / "experiments")
            record["experiments"].append({"fault": fault, "experiment": str(experiment),
                                          "status": outcome["status"],
                                          "recovered": outcome.get("phases", {}).get("recovered", {}).get("passed") is True})
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if outcome["status"] != "PASS" or not record["experiments"][-1]["recovered"]:
                record["errors"].append(f"{fault}: experiment or restoration failed; no further injection")
                break
        verify()
        final = collect_evidence(LAB_DIR, root / "final-network")
        metrics = metrics_from_evidence(read_layout(LAB_DIR), final)
        record["final_metrics"] = metrics
        record["final_network"] = "RESTORED" if healthy(metrics) else "UNHEALTHY"
    except BaseException as exc:
        record["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        record["finished_at_utc"] = utc_now()
        record["status"] = ("PASS" if len(record["experiments"]) == 3 and
                            all(item["status"] == "PASS" and item["recovered"] for item in record["experiments"]) and
                            record["final_network"] == "RESTORED" and not record["errors"] else "FAIL")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Status: {record['status']}")
    print(f"Final network: {record['final_network']}")
    print(f"Batch: {path}")
    if record["errors"]:
        print("Errors: " + " | ".join(record["errors"]), file=sys.stderr)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
