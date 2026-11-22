#!/usr/bin/env python3
"""Export a diagnosis as a self-contained handoff bundle for netcfg-evidence.

The bundle carries the conclusion, cited observations, the target and peer
config snapshots and the link the diagnosis refers to. Every observation keeps
the stored stdout/stderr, a truncation marker and a digest computed over that
exact stored content, so the receiver can recompute it instead of trusting an
excerpt. The bundle hash covers the canonical JSON.
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

MAX_STREAM_BYTES = 50_000


class HandoffError(ValueError):
    pass


def canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def trace_identity(trace: dict[str, Any]) -> str:
    """Stable identity of the sampled observations, independent of the export
    timestamp and the model's conclusion."""
    body = {"task": trace.get("task"), "initial_topology": trace.get("initial_topology"),
            "evidence": trace.get("evidence")}
    return hashlib.sha256(canonical(body)).hexdigest()


def _bounded(text: str, limit: int = MAX_STREAM_BYTES) -> tuple[str, bool, int]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text, False, len(encoded)
    return encoded[:limit].decode("utf-8", "ignore"), True, len(encoded)


def _observation_payload(observation: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    raw_stdout = str(observation.get("stdout") or "")
    raw_stderr = str(observation.get("stderr") or "")
    upstream_stdout_truncated = bool(observation.get("stdout_truncated"))
    upstream_stdout_bytes = observation.get("stdout_bytes")
    if full:
        stdout, stdout_truncated, stdout_bytes = raw_stdout, False, len(raw_stdout.encode("utf-8"))
    elif upstream_stdout_truncated:
        stdout = raw_stdout
        stdout_truncated = True
        stdout_bytes = int(upstream_stdout_bytes or len(raw_stdout.encode("utf-8")))
    else:
        stdout, stdout_truncated, stdout_bytes = _bounded(raw_stdout)
    upstream_stderr_truncated = bool(observation.get("stderr_truncated"))
    upstream_stderr_bytes = observation.get("stderr_bytes")
    if upstream_stderr_truncated:
        stderr = raw_stderr
        stderr_truncated = True
        stderr_bytes = int(upstream_stderr_bytes or len(raw_stderr.encode("utf-8")))
    else:
        stderr, stderr_truncated, stderr_bytes = _bounded(raw_stderr)
    return {
        "node": observation.get("node"),
        "command": [str(part) for part in observation.get("command") or []],
        "exit_code": observation.get("exit_code"),
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
    }


def observation_digest(observations: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical(observations)).hexdigest()


def _citation(evidence_id: str, record: dict[str, Any],
              observations: list[dict[str, Any]], node: str | None) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "tool": record.get("tool"),
        "arguments": record.get("arguments") or {},
        "node": node,
        "observations": observations,
        "content_sha256": observation_digest(observations),
    }


def _evidence_items(trace: dict[str, Any], ids: list[str], skip: set[str]) -> list[dict[str, Any]]:
    evidence = trace.get("evidence") or {}
    items = []
    seen: set[str] = set(skip)
    for evidence_id in ids:
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in seen:
            continue
        record = evidence.get(evidence_id)
        if not isinstance(record, dict):
            continue
        seen.add(evidence_id)
        observations = [_observation_payload(item) for item in record.get("observations") or []
                        if isinstance(item, dict)]
        node = (record.get("arguments") or {}).get("node")
        items.append(_citation(evidence_id, record, observations, node))
    return items


def _config_snapshot(trace: dict[str, Any], node: str) -> tuple[str, str, dict[str, Any]] | None:
    """Pick a successful running-config observation for the requested node."""
    for evidence_id, record in (trace.get("evidence") or {}).items():
        if record.get("tool") != "query_interface_config" or record.get("status") != "ok":
            continue
        if (record.get("arguments") or {}).get("node") != node:
            continue
        for observation in record.get("observations") or []:
            if observation.get("exit_code") != 0:
                continue
            if observation.get("stdout_truncated"):
                continue
            stdout_bytes = observation.get("stdout_bytes")
            command = [str(part) for part in observation.get("command") or []]
            if not any("show running-config" in part for part in command):
                continue
            text = str(observation.get("stdout") or "")
            if isinstance(stdout_bytes, int) and stdout_bytes != len(text.encode("utf-8")):
                continue
            if "Current configuration" in text:
                return text, evidence_id, observation
    return None


def _observation_citation(evidence_id: str, record: dict[str, Any],
                          observation: dict[str, Any], node: str | None) -> dict[str, Any]:
    return _citation(evidence_id, record, [_observation_payload(observation, full=True)], node)


def _topology_link(trace: dict[str, Any], target: str) -> dict[str, Any]:
    links = (trace.get("initial_topology") or {}).get("link") or []
    local = next((item for item in links if isinstance(item, dict) and item.get("node") == target), None)
    peer = next((item for item in links if isinstance(item, dict) and item.get("node") != target), None)
    return {
        "node": target,
        "interface": (local or {}).get("interface"),
        "peer": (peer or {}).get("node"),
        "peer_interface": (peer or {}).get("interface"),
    }


def derive_commands(trace: dict[str, Any], status: str, target: str | None) -> list[str]:
    if status not in PLANNABLE or not target:
        return []
    facts = collect_facts(trace)
    interface = (trace.get("initial_topology") or {}).get("link") or []
    iface = next((item.get("interface") for item in interface if item.get("node") == target), None)
    if not iface:
        return []
    try:
        if status == "ospf_area_mismatch":
            peer = "r2" if target == "r1" else "r1"
            current = (facts.get(target) or {}).get("area")
            peer_area = (facts.get(peer) or {}).get("area")
            return list(build_plan(status, target, iface, current_area=current, peer_area=peer_area).commands)
        return list(build_plan(status, target, iface).commands)
    except ValueError:
        # A trace built only from running-config observations has no OSPF facts;
        # the external command suggestion is optional, the snapshots are the contract.
        return []


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
    if device != target:
        raise HandoffError("Device name must match the snapshot target; peer evidence belongs in citations")
    snapshot = _config_snapshot(trace, target)
    if not snapshot:
        raise HandoffError(f"Trace has no successful running-config evidence for target {target}; "
                           "refusing to hand off a peer snapshot as the target device")
    config, evidence_id, snapshot_observation = snapshot
    snapshot_record = (trace.get("evidence") or {}).get(evidence_id) or {}
    snapshot_node = (snapshot_record.get("arguments") or {}).get("node")
    if commands is None:
        commands = derive_commands(trace, status, target)
    if commands:
        from simulate_fix import COMMAND
        for command in commands:
            if not COMMAND.match(command):
                raise HandoffError(f"Command is not allowlisted: {command!r}")

    link = _topology_link(trace, target)
    citations = [_observation_citation(evidence_id, snapshot_record, snapshot_observation, snapshot_node)]
    skip = {evidence_id}

    peer_snapshot = None
    peer_node = link.get("peer")
    if isinstance(peer_node, str) and peer_node:
        peer_pick = _config_snapshot(trace, peer_node)
        if peer_pick:
            peer_text, peer_evidence_id, peer_observation = peer_pick
            peer_record = (trace.get("evidence") or {}).get(peer_evidence_id) or {}
            citations.append(_observation_citation(peer_evidence_id, peer_record, peer_observation,
                                                   (peer_record.get("arguments") or {}).get("node")))
            skip.add(peer_evidence_id)
            peer_snapshot = {
                "node": peer_node,
                "interface": link.get("peer_interface"),
                "evidence_id": peer_evidence_id,
                "command": [str(part) for part in peer_observation.get("command") or []],
                "exit_code": peer_observation.get("exit_code"),
                "text": peer_text,
                "sha256": hashlib.sha256(peer_text.encode("utf-8")).hexdigest(),
            }

    cited_ids = [item for item in final.get("evidence_ids") or [] if isinstance(item, str)]
    citations.extend(_evidence_items(trace, cited_ids, skip))

    payload = {
        "schema_version": 3,
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
            "command": [str(part) for part in snapshot_observation.get("command") or []],
            "exit_code": snapshot_observation.get("exit_code"),
            "text": config,
            "sha256": hashlib.sha256(config.encode("utf-8")).hexdigest(),
        },
        "peer_snapshot": peer_snapshot,
        "proposed_commands": commands,
        "citations": citations,
        "link": link,
        "lab_only": True,
    }
    payload["bundle_sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
    return payload


def _check_observation_digests(citations: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for item in citations:
        evidence_id = item.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise HandoffError("citation has no evidence_id")
        if evidence_id in seen:
            raise HandoffError(f"duplicate citation evidence_id: {evidence_id}")
        seen.add(evidence_id)
        observations = item.get("observations")
        if not isinstance(observations, list) or not observations:
            raise HandoffError(f"citation {evidence_id} has no observations")
        if observation_digest(observations) != item.get("content_sha256"):
            raise HandoffError(f"citation {evidence_id} digest does not match its observations")


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("source") != "netsecops-agent":
        raise HandoffError("Bundle source is not netsecops-agent")
    if bundle.get("schema_version") != 3:
        raise HandoffError("Unsupported bundle schema version")
    expected = bundle.get("bundle_sha256")
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if expected != hashlib.sha256(canonical(body)).hexdigest():
        raise HandoffError("Bundle hash does not match its contents")
    if not isinstance(bundle.get("trace_id"), str) or len(bundle["trace_id"]) != 64:
        raise HandoffError("Bundle has no valid trace_id")

    snapshot = bundle.get("config_snapshot") or {}
    if snapshot.get("node") != snapshot.get("target"):
        raise HandoffError("Snapshot node and target differ")
    config = snapshot.get("text") or ""
    if hashlib.sha256(config.encode("utf-8")).hexdigest() != snapshot.get("sha256"):
        raise HandoffError("Config snapshot hash does not match")
    if snapshot.get("exit_code") != 0:
        raise HandoffError("Snapshot command did not succeed")
    if not any("show running-config" in str(part) for part in snapshot.get("command") or []):
        raise HandoffError("Snapshot command is not a running-config collection")

    peer = bundle.get("peer_snapshot")
    if peer is not None:
        if peer.get("node") != (bundle.get("link") or {}).get("peer"):
            raise HandoffError("Peer snapshot node does not match the link")
        if hashlib.sha256((peer.get("text") or "").encode("utf-8")).hexdigest() != peer.get("sha256"):
            raise HandoffError("Peer snapshot hash does not match")
        if peer.get("exit_code") != 0:
            raise HandoffError("Peer snapshot command did not succeed")

    _check_observation_digests(bundle.get("citations") or [])
    return bundle


def require_peer_snapshot(bundle: dict[str, Any], expected_status: str | None = None) -> None:
    peer = bundle.get("peer_snapshot")
    if not isinstance(peer, dict) or not peer.get("text"):
        raise HandoffError("handoff has no complete peer snapshot; refusing to export")
    status = (bundle.get("diagnosis") or {}).get("status")
    if expected_status is not None and status != expected_status:
        raise HandoffError(f"diagnosis status {status!r} does not match expected {expected_status!r}; "
                           "refusing to hand off")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--target", choices=("r1", "r2"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--require-peer", action="store_true",
                        help="Require a complete peer snapshot (both ends)")
    parser.add_argument("--expect-status", default=None,
                        help="Refuse unless the diagnosis status equals this value")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.trace.read_text(encoding="utf-8"))
        trace = document.get("trace") if isinstance(document.get("trace"), dict) else document
        bundle = build_bundle(trace, args.device, target=args.target)
        if args.require_peer:
            require_peer_snapshot(bundle, expected_status=args.expect_status)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, HandoffError, ValueError) as exc:
        print(f"Handoff error: {exc}", file=sys.stderr)
        return 2
    print(f"Bundle: {args.out}  sha256={bundle['bundle_sha256'][:12]}  commands={len(bundle['proposed_commands'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
