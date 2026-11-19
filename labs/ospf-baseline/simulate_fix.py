#!/usr/bin/env python3
"""Lab-only remediation simulation: plan -> apply -> re-check -> roll back.

This is intentionally not a remediation tool for production. It only runs
against the Containerlab OSPF lab, only accepts fixed status-derived commands,
and always rolls back to the pre-simulation state. Offline tests exercise the
flow with a fake executor; the Docker path is gated by an explicit env flag.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Protocol

LAB_DIR = Path(__file__).resolve().parent
PLANNABLE = ("ospf_area_mismatch", "interface_admin_down", "ospf_passive")
AREA = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
COMMAND = re.compile(
    r"^(?:no )?(?:shutdown|ip ospf passive|ip ospf area \d{1,3}(?:\.\d{1,3}){3})$")


class SimulationError(ValueError):
    pass


@dataclass(frozen=True)
class FixPlan:
    status: str
    target: str
    interface: str
    commands: tuple[str, ...]
    inverse: tuple[str, ...]
    rationale: str


def _check(commands: list[str]) -> tuple[str, ...]:
    for command in commands:
        if not COMMAND.match(command):
            raise SimulationError(f"Command is not in the fixed simulation allowlist: {command!r}")
    return tuple(commands)


def build_plan(status: str, target: str, interface: str, *,
               current_area: str | None = None, peer_area: str | None = None) -> FixPlan:
    if status not in PLANNABLE:
        raise SimulationError(f"Status {status!r} has no safe simulation plan")
    if target not in ("r1", "r2"):
        raise SimulationError("Target must be one of the two lab nodes")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", interface):
        raise SimulationError("Interface name is not a valid lab interface")
    if status == "ospf_area_mismatch":
        if not current_area or not peer_area or not AREA.match(current_area) or not AREA.match(peer_area):
            raise SimulationError("Area simulation needs two valid IPv4 OSPF areas")
        commands = _check([f"no ip ospf area {current_area}", f"ip ospf area {peer_area}"])
        inverse = _check([f"no ip ospf area {peer_area}", f"ip ospf area {current_area}"])
        rationale = f"Align {target} {interface} from Area {current_area} to peer Area {peer_area}"
    elif status == "interface_admin_down":
        commands = _check(["no shutdown"])
        inverse = _check(["shutdown"])
        rationale = f"Bring {target} {interface} out of administrative shutdown"
    else:
        commands = _check(["no ip ospf passive"])
        inverse = _check(["ip ospf passive"])
        rationale = f"Remove OSPF passive on {target} {interface}"
    return FixPlan(status, target, interface, commands, inverse, rationale)


class Executor(Protocol):
    def run(self, node: str, interface: str, commands: tuple[str, ...]) -> dict[str, Any]: ...


class FakeExecutor:
    """Test double: records commands and can fail on demand."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_on = fail_on
        self._state = 0

    def run(self, node: str, interface: str, commands: tuple[str, ...]) -> dict[str, Any]:
        if self.fail_on and self.fail_on in commands:
            raise SimulationError(f"Fake executor rejected {commands}")
        self.calls.append({"node": node, "interface": interface, "commands": list(commands)})
        self._state += 1
        return {"exit_code": 0, "commands": list(commands), "call": self._state}


class DockerExecutor:
    """Apply fixed vtysh commands to one lab container."""

    def __init__(self, layout: Any, *, enabled: bool) -> None:
        if not enabled:
            raise SimulationError("Docker executor is disabled; pass --confirm-lab and set NETSECOPS_LAB_SIMULATION=1")
        self.layout = layout

    def run(self, node: str, interface: str, commands: tuple[str, ...]) -> dict[str, Any]:
        if node not in self.layout.nodes:
            raise SimulationError("Node is not part of the lab topology")
        if interface != self.layout.interfaces.get(node):
            raise SimulationError("Interface is not the lab interconnect interface")
        for command in commands:
            if not COMMAND.match(command):
                raise SimulationError(f"Command is not allowlisted: {command!r}")
        container = self.layout.container(node)
        if not container.startswith(f"clab-{self.layout.lab_name}-"):
            raise SimulationError("Container name is not part of this lab")
        argv = ["sudo", "-n", "docker", "exec", container, "vtysh",
                "-c", "configure terminal", "-c", f"interface {interface}"]
        for command in commands:
            argv += ["-c", command]
        argv += ["-c", "end"]
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=25, check=False)
        rejected = re.search(r"(?im)^(?:%\s*)?(?:unknown command|invalid input|error:)\b", done.stdout)
        if done.returncode != 0 or rejected:
            raise SimulationError(f"FRR rejected {commands}: exit={done.returncode}; "
                                  f"{done.stdout.strip()} {done.stderr.strip()}")
        return {"exit_code": done.returncode, "commands": list(commands),
                "stdout": done.stdout[-2000:], "stderr": done.stderr[-2000:]}


def require_lab_confirmation(env: dict[str, str] | None = None) -> None:
    env = env if env is not None else os.environ
    if env.get("NETSECOPS_LAB_SIMULATION") != "1":
        raise SimulationError("Set NETSECOPS_LAB_SIMULATION=1 to allow lab-only simulation")


def simulate(plan: FixPlan, executor: Executor,
             collect: Callable[[], dict[str, Any]],
             diagnose: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Apply, re-check, always roll back, then re-check the rollback."""
    before = collect()
    start = diagnose(before)
    if start.get("status") != plan.status:
        raise SimulationError(f"Pre-simulation diagnosis is {start.get('status')!r}, expected {plan.status!r}; "
                              "refusing to change the lab")
    applied = executor.run(plan.target, plan.interface, plan.commands)
    after = collect()
    after_status = diagnose(after).get("status")
    rollback_error = None
    try:
        rolled_back = executor.run(plan.target, plan.interface, plan.inverse)
    except Exception as exc:  # rollback must be attempted even when verification failed
        rolled_back = None
        rollback_error = f"rollback failed: {type(exc).__name__}: {exc}"
    restored = collect()
    restored_status = diagnose(restored).get("status")
    success = after_status == "normal"
    restored_ok = restored_status == plan.status
    if rollback_error or not restored_ok:
        success = False
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "PASS" if success else "FAIL",
        "scenario": plan.status,
        "target": plan.target,
        "interface": plan.interface,
        "rationale": plan.rationale,
        "commands": list(plan.commands),
        "inverse_commands": list(plan.inverse),
        "before_status": start.get("status"),
        "after_status": after_status,
        "restored_status": restored_status,
        "restored": bool(rollback_error is None and restored_ok),
        "rollback_error": rollback_error,
        "applied": applied,
        "rolled_back": rolled_back,
        "lab_only": True,
    }


def _live_diagnose(document: dict[str, Any]) -> dict[str, Any]:
    from diagnose_offline import diagnose
    return diagnose(document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("r1", "r2"), required=True,
                        help="Node whose interface the fix is simulated on")
    parser.add_argument("--out", type=Path, default=Path("simulation-runs"))
    parser.add_argument("--confirm-lab", action="store_true",
                        help="Required acknowledgement that this only touches the lab")
    args = parser.parse_args(argv)
    try:
        require_lab_confirmation()
        if not args.confirm_lab:
            raise SimulationError("Refusing to change the lab without --confirm-lab")
        from collect_evidence import collect_evidence, read_layout
        from diagnose_offline import diagnose

        layout = read_layout(LAB_DIR)
        out = args.out / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out.mkdir(parents=True, exist_ok=False)
        before_dir = out / "before"
        before = collect_evidence(LAB_DIR, before_dir)
        status = diagnose(before).get("status")
        interface = layout.interfaces[args.target]
        current_area = peer_area = None
        if status == "ospf_area_mismatch":
            info = (before.get("discovered") or {}).get(args.target) or {}
            peer = layout.peer(args.target)
            peer_info = (before.get("discovered") or {}).get(peer) or {}
            current_area, peer_area = info.get("link_area"), peer_info.get("link_area")
        plan = build_plan(status, args.target, interface,
                          current_area=current_area, peer_area=peer_area)
        executor = DockerExecutor(layout, enabled=True)

        def collect_after() -> dict[str, Any]:
            directory = out / ("after" if not (out / "after").exists() else "restored")
            directory.mkdir(exist_ok=True)
            return collect_evidence(LAB_DIR, directory)

        report = simulate(plan, executor, collect_after, diagnose)
        report["before_observations"] = str(before_dir)
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        print(f"Simulation: {report['status']}  {report['before_status']} -> {report['after_status']} "
              f"-> restored {report['restored_status']}")
        print(f"Report: {out / 'report.json'}")
        return 0 if report["status"] == "PASS" else 1
    except SimulationError as exc:
        print(f"Simulation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
