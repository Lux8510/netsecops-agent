#!/usr/bin/env python3
"""Export a diagnosis as a self-contained handoff bundle for netcfg-evidence.

The bundle carries the conclusion, cited evidence, the config snapshot and
(optionally) a fixed remediation plan. It contains no credentials or host paths
from the operator workstation, and the bundle hash covers the canonical JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

from evidence_semantics import _text, collect_facts
from simulate_fix import PLANNABLE, build_plan

MAX_EXCERPT = 600


class HandoffError(ValueError):
    pass


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def trace_identity(trace: dict[str, Any]) -> str:
    """Stable identity of the sampled observations, independent of the export
    timestamp and the model's conclusion. Re-exporting the same trace yields the
    same id, so netcfg can deduplicate a re-imported diagnosis."""
    body = {"task": trace.get("task"), "initial_topology": trace.get("initial_topology"),
            "evidence": trace.get("evidence")}
    return hashlib.sha256(canonical(body)).hexdigest()


def _evidence_items(trace: dict[str, Any]) -> list[dict[str, Any]]:
    final = trace.get("final") or {}
    ids = [item for item in final.get("evidence_ids") or [] if isinstance(item, str)]
    evidence = trace.get("evidence") or {}
    items = []
    for evidence_id in ids:
        record = evidence.get(evidence_id)
        if not isinstance(record, dict):
            continue
        observations = [item for item in record.get("observations") or [] if isinstance(item, dict)]
        first = observations[0] if observations else {}
        node = (record.get("arguments") or {}).get("node")
        full = _text(record)
        items.append({
            "evidence_id": evidence_id,
            "tool": record.get("tool"),
            "arguments": record.get("arguments") or {},
            "node": node,
            "command": first.get("command") or [],
            "exit_code": first.get("exit_code"),
            "content_sha256": hashlib.sha256(full.encode("utf-8")).hexdigest(),
            "excerpt": full[:MAX_EXCERPT],
        })
    return items


def _config_snapshot(trace: dict[str, Any], node: str) -> tuple[str, str, list[str], int] | None:
    """Pick a successful running-config observation for the requested node."""
    for evidence_id, record in (trace.get("evidence") or {}).items():
        if record.get("tool") != "query_interface_config" or record.get("status") != "ok":
            continue
        if (record.get("arguments") or {}).get("node") != node:
            continue
        for observation in record.get("observations") or []:
            if observation.get("exit_code") != 0:
                continue
            command = [str(part) for part in observation.get("command") or []]
            if not any("show running-config" in part for part in command):
                continue
            text = str(observation.get("stdout") or "")
            if "Current configuration" in text:
                return text, evidence_id, command, int(observation.get("exit_code"))
    return None


def derive_commands(trace: dict[str, Any], status: str, target: str | None) -> list[str]:
    if status not in PLANNABLE or not target:
        return []
    facts = collect_facts(trace)
    interface = (trace.get("initial_topology") or {}).get("link") or []
    iface = next((item.get("interface") for item in interface if item.get("node") == target), None)
    if not iface:
        return []
    if status == "ospf_area_mismatch":
        peer = "r2" if target == "r1" else "r1"
        current = (facts.get(target) or {}).get("area")
        peer_area = (facts.get(peer) or {}).get("area")
        return list(build_plan(status, target, iface, current_area=current, peer_area=peer_area).commands)
    return list(build_plan(status, target, iface).commands)


def build_bundle(trace: dict[str, Any], device: str, *, target: str | None = None,
                 commands: list[str] | None = None) -> dict[str, Any]:
    final = trace.get("final") or {}
    status = final.get("status")
    if status not in (*PLANNABLE, "normal", "insufficient_evidence", "collection_failure"):
        raise HandoffError(f"Unsupported diagnosis status: {status!r}")
    target = target or next(iter([item.get("node") for item in (trace.get("initial_topology") or {}).get("link") or []
                                  if isinstance(item.get("node"), str)]), None)
    if not target:
        raise HandoffError("A target node is required to select the config snapshot")
    snapshot = _config_snapshot(trace, target)
    if not snapshot:
        raise HandoffError(f"Trace has no successful running-config evidence for target {target}; "
                           "refusing to hand off a peer snapshot as the target device")
    if device != target:
        raise HandoffError("Device name must match the snapshot target; peer evidence belongs in citations")
    config, evidence_id, snapshot_command, snapshot_exit = snapshot
    snapshot_node = next(
        (record.get("arguments", {}).get("node") for record in (trace.get("evidence") or {}).values()
         if record.get("evidence_id") == evidence_id), None)
    if commands is None:
        commands = derive_commands(trace, status, target)
    if commands:
        # Reuse the fixed simulation allowlist; no arbitrary commands in a handoff.
        from simulate_fix import COMMAND
        for command in commands:
            if not COMMAND.match(command):
                raise HandoffError(f"Command is not allowlisted: {command!r}")
    payload = {
        "schema_version": 2,
        "source": "netsecops-agent",
        "trace_id": trace_identity(trace),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": {"name": device},
        "diagnosis": {
            "status": status,
            "conclusion": final.get("conclusion"),
            "repair_suggestion": final.get("repair_suggestion"),
            "evidence_ids": final.get("evidence_ids") or [],
        },
        "config_snapshot": {
            "node": snapshot_node,
            "target": target,
            "evidence_id": evidence_id,
            "command": snapshot_command,
            "exit_code": snapshot_exit,
            "text": config,
            "sha256": hashlib.sha256(config.encode("utf-8")).hexdigest(),
        },
        "proposed_commands": commands,
        "citations": _evidence_items(trace),
        "lab_only": True,
    }
    payload["bundle_sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    return payload


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("source") != "netsecops-agent":
        raise HandoffError("Bundle source is not netsecops-agent")
    if bundle.get("schema_version") != 2:
        raise HandoffError("Unsupported bundle schema version")
    expected = bundle.get("bundle_sha256")
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    actual = hashlib.sha256(canonical(body)).hexdigest()
    if expected != actual:
        raise HandoffError("Bundle hash does not match its contents")
    snapshot = bundle.get("config_snapshot") or {}
    if not isinstance(bundle.get("trace_id"), str) or len(bundle.get("trace_id")) != 64:
        raise HandoffError("Bundle has no valid trace_id")
    config = snapshot.get("text") or ""
    if hashlib.sha256(config.encode("utf-8")).hexdigest() != snapshot.get("sha256"):
        raise HandoffError("Config snapshot hash does not match")
    if not isinstance(snapshot.get("node"), str) or not isinstance(snapshot.get("target"), str):
        raise HandoffError("Snapshot must bind node and target")
    if snapshot.get("node") != snapshot.get("target"):
        raise HandoffError("Snapshot node and target differ")
    if not isinstance(snapshot.get("evidence_id"), str) or not snapshot["evidence_id"]:
        raise HandoffError("Snapshot has no evidence_id")
    command = snapshot.get("command")
    if not isinstance(command, list) or not any("show running-config" in str(part) for part in command):
        raise HandoffError("Snapshot command is not a running-config collection")
    if snapshot.get("exit_code") != 0:
        raise HandoffError("Snapshot command did not succeed")
    return bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--target", choices=("r1", "r2"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.trace.read_text(encoding="utf-8"))
        trace = document.get("trace") if isinstance(document.get("trace"), dict) else document
        bundle = build_bundle(trace, args.device, target=args.target)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, HandoffError, ValueError) as exc:
        print(f"Handoff error: {exc}", file=sys.stderr)
        return 2
    print(f"Bundle: {args.out}  sha256={bundle['bundle_sha256'][:12]}  commands={len(bundle['proposed_commands'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
