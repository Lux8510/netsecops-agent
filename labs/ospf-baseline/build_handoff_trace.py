#!/usr/bin/env python3
"""Build a handoff-ready trace from one collector observations.json.

This is the deterministic link between real collection and handoff: it copies
the collected running-config observations for both nodes into a trace whose
final status comes from the offline rules diagnosis. No model key is needed.

Usage:
    python build_handoff_trace.py --observations /tmp/live/observations.json \
      --target r2 --out /tmp/area-trace.json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from diagnose_offline import diagnose


def _node_config(document: dict[str, Any], node: str) -> tuple[dict[str, Any], str] | None:
    commands = (document.get("nodes") or {}).get(node, {}).get("commands") or []
    for item in commands:
        if item.get("id") != "running_config":
            continue
        command = [str(part) for part in item.get("command") or []]
        if not any("show running-config" in part for part in command):
            continue
        if item.get("exit_code") != 0:
            continue
        if "Current configuration" not in str(item.get("stdout") or ""):
            continue
        return item, command
    return None


def build_trace(document: dict[str, Any], target: str) -> dict[str, Any]:
    if document.get("schema_version") != 1 or not isinstance(document.get("nodes"), dict):
        raise ValueError("observations.json has an unsupported schema")
    discovered = document.get("discovered") or {}
    if target not in discovered:
        raise ValueError(f"target {target!r} is not in the collected topology")
    nodes = list(document["nodes"].keys())
    evidence: dict[str, Any] = {}
    evidence_ids: list[str] = []
    link: list[dict[str, str]] = []
    for index, node in enumerate(nodes, start=1):
        info = discovered.get(node) or {}
        interface = info.get("link_interface")
        if isinstance(interface, str):
            link.append({"node": node, "interface": interface})
        picked = _node_config(document, node)
        if picked is None:
            continue
        item, command = picked
        evidence_id = f"C{index:02d}"
        evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "collected_at_utc": item.get("started_at_utc") or document.get("collected_at_utc"),
            "tool": "query_interface_config",
            "arguments": {"node": node, "interface": interface or ""},
            "status": "ok",
            "data": None,
            "observations": [{
                "node": node,
                "command": command,
                "exit_code": item.get("exit_code"),
                "started_at_utc": item.get("started_at_utc"),
                "ended_at_utc": item.get("ended_at_utc"),
                "stdout": item.get("stdout"),
                "stderr": item.get("stderr") or "",
                "error": item.get("error"),
            }],
            "error": None,
            "untrusted": {"count": 0, "matches": []},
        }
        evidence_ids.append(evidence_id)

    diagnosis = diagnose(document)
    trace = {
        "schema_version": 1,
        "mode": "collected-observations",
        "task": f"从保存的只读采集构建交接轨迹；target={target}",
        "started_at_utc": document.get("collected_at_utc") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_requested": "none",
        "model_reported": "none",
        "initial_topology": {"lab": document.get("lab"), "nodes": nodes, "link": link},
        "settings": {},
        "rounds": [],
        "tool_calls_executed": len(evidence_ids),
        "usage_per_request": [],
        "usage_total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                        "requests_with_usage": 0},
        "model_raw_final": None,
        "final": {
            "status": diagnosis.get("status"),
            "conclusion": diagnosis.get("conclusion"),
            "repair_suggestion": diagnosis.get("repair_suggestion"),
            "evidence_ids": evidence_ids,
        },
        "guardrail_changes": [],
        "elapsed_seconds": 0,
        "evidence": evidence,
        "error": None,
    }
    return trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--target", choices=("r1", "r2"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.observations.read_text(encoding="utf-8"))
        trace = build_trace(document, args.target)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Trace build error: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Trace: {args.out}  status={trace['final']['status']}  configs={len(trace['evidence'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
