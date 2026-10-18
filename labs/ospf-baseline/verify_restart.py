#!/usr/bin/env python3
"""Verify this lab's restart persistence; repair only confirmed stale FRR startup files."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from typing import Any

from collect_evidence import LAB_DIR, collect_evidence, ensure_wsl2_and_sudo, parse_node_config, read_layout, utc_now
from run_area_mismatch import (healthy, host_command, live_probe, metrics_from_evidence,
                               preflight, startup_files, write_json)


TOPOLOGY = LAB_DIR / "topology.clab.yml"
OUTPUT_ROOT = LAB_DIR / "restart-verification"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def restart(layout: Any) -> dict[str, Any]:
    argv = ["sudo", "-n", "containerlab", "restart", "-t", str(TOPOLOGY)]
    for node in layout.nodes:
        argv += ["--node", node]
    done = host_command(argv, timeout=180)
    return {"at_utc": utc_now(), "command": argv, "exit_code": done.returncode,
            "stdout": done.stdout, "stderr": done.stderr}


def mount_sources(layout: Any, run_dir: Path) -> dict[str, str]:
    raw = host_command(["sudo", "-n", "docker", "inspect", *[layout.container(n) for n in layout.nodes]]).stdout
    (run_dir / "docker-inspect.json").write_text(raw, encoding="utf-8")
    inspected = json.loads(raw)
    if len(inspected) != 2:
        raise RuntimeError("docker inspect did not return exactly two nodes")
    sources = {}
    for node, item in zip(layout.nodes, inspected):
        if item.get("Name") != "/" + layout.container(node):
            raise RuntimeError(f"Unexpected Docker identity for {node}")
        mounts = [m for m in item.get("Mounts", []) if m.get("Destination") == "/etc/frr"]
        expected = (LAB_DIR / f"clab-{layout.lab_name}" / node / "config").resolve()
        if len(mounts) != 1 or mounts[0].get("Type") != "bind" or Path(mounts[0].get("Source", "")).resolve() != expected:
            raise RuntimeError(f"{node}: /etc/frr is not bound to expected generated config directory")
        sources[node] = mounts[0]["Source"]
    return sources


def wait_healthy(layout: Any, details: dict[str, Any], path: Path, seconds: int = 90) -> bool:
    samples = []
    deadline = time.monotonic() + seconds
    try:
        while True:
            try:
                sample = live_probe(layout, details)
                metrics = metrics_from_evidence(layout, {"nodes": sample["nodes"], "discovered": details,
                                                         "discovery_error": None})
                samples.append({"sample": sample, "metrics": metrics})
                if healthy(metrics):
                    return True
            except Exception as exc:
                samples.append({"at_utc": utc_now(), "probe_error": f"{type(exc).__name__}: {exc}"})
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(3, max(0, deadline - time.monotonic())))
    finally:
        write_json(path, {"schema_version": 1, "samples": samples})


def config_guard(layout: Any, initial: dict[str, Any], after: dict[str, Any],
                 initial_hashes: dict[str, dict[str, str]]) -> None:
    """Require the known old-interface-mode regression before touching startup files."""
    if after.get("discovery_error") or "discovered" not in after:
        raise RuntimeError("Restarted configuration cannot be discovered; no automatic repair")
    after_metrics = metrics_from_evidence(layout, after)
    for node in layout.nodes:
        old = initial["discovered"][node]
        current = after["discovered"][node]
        paths = startup_files(layout, node)
        source = parse_node_config(paths["source"].read_text(encoding="utf-8"), layout.interfaces[node])
        generated = parse_node_config(paths["deployed"].read_text(encoding="utf-8"), layout.interfaces[node])
        if old["ospf_mode"] != "network" or source["ospf_mode"] != "network":
            raise RuntimeError(f"{node}: source or pre-restart runtime is not network-statement mode")
        if current["ospf_mode"] != "interface" or generated["ospf_mode"] != "interface":
            raise RuntimeError(f"{node}: restart did not reveal the expected old interface-area mode")
        for key in ("link_interface", "link_address", "link_prefix", "link_area", "loopback_address", "router_id"):
            if len({old[key], current[key], source[key], generated[key]}) != 1:
                raise RuntimeError(f"{node}: {key} differs; refusing automatic repair")
        if sha(paths["source"]) != initial_hashes[node]["source"] or sha(paths["deployed"]) != initial_hashes[node]["deployed"]:
            raise RuntimeError(f"{node}: startup file changed during check; refusing automatic repair")
        if not after_metrics[node]["peer_link_ping"] or after_metrics[node]["remote_loopback_route"]:
            raise RuntimeError(f"{node}: observed failure is not the expected route-regression signature")


def render(record: dict[str, Any], run_dir: Path) -> None:
    lines = ["# 路由器重启恢复验收", "", f"状态：**{record['status']}**", "",
             f"开始：`{record['started_at_utc']}`", f"结束：`{record.get('finished_at_utc')}`", "",
             "仅对本实验的两台路由器使用 Containerlab 节点级 restart；未重启 Docker、WSL 或主机。", "",
             "## 启动配置来源", ""]
    for node, source in record.get("mount_sources", {}).items():
        lines.append(f"- {node}: Docker bind mount `{source}` → `/etc/frr`；实际启动文件为其中的 `frr.conf`。")
    lines += ["", "## 实际步骤", ""]
    for event in record.get("events", []):
        lines.append(f"- {event}")
    lines += ["", "## 结果", ""]
    for stage in ("before", "after_first_restart", "after_repair_restart"):
        phase = record.get("phases", {}).get(stage)
        if phase:
            lines.append(f"- {stage}: healthy={phase.get('healthy')}；metrics={json.dumps(phase.get('metrics'), ensure_ascii=False)}")
    if record.get("errors"):
        lines += ["", "## 错误与现场状态", ""]
        lines += [f"- {item}" for item in record["errors"]]
    lines += ["", "## 原始证据", "", "- `docker-inspect.json`：实际挂载。",
              "- `startup-before/`：两台路由器源配置与生成配置的原始副本。",
              "- `before/`、`after-first-restart/`、`after-repair-restart/`：存在时为完整观测。",
              "- `first-poll.json`、`repair-poll.json`：有时间上限的收敛记录。", ""]
    if record.get("status") != "PASS":
        lines += ["## 手动恢复", "", "先查看本报告与最后一组 `observations.json`。确认本实验两台节点的 `/etc/frr` 挂载来源后，",
                  "可将本目录 `startup-before/` 的原件与项目 `configs/<node>/frr.conf` 对比。仅当确认配置一致且需修复时，",
                  "把项目源配置复制到对应的 `clab-netsecops-ospf/<node>/config/frr.conf`，",
                  "用 `sudo containerlab restart -t topology.clab.yml --node r1 --node r2` 仅重启两节点，",
                  "再运行 `collect_evidence.py` 检查邻居、双向路由和 ping。不要执行 destroy、reconfigure 或 write memory。", ""]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run() -> tuple[Path, dict[str, Any]]:
    ensure_wsl2_and_sudo()
    layout = read_layout(LAB_DIR)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = OUTPUT_ROOT / f"restart-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    record: dict[str, Any] = {"schema_version": 1, "started_at_utc": utc_now(),
                              "status": "IN_PROGRESS", "events": [], "errors": [], "phases": {}}
    try:
        record["preflight"] = preflight(layout)
        record["mount_sources"] = mount_sources(layout, run_dir)
        original_hashes = {}
        snapshot = run_dir / "startup-before"
        snapshot.mkdir()
        for node in layout.nodes:
            original_hashes[node] = {}
            for label, path in startup_files(layout, node).items():
                data = path.read_bytes()
                (snapshot / f"{node}-{label}-frr.conf").write_bytes(data)
                original_hashes[node][label] = hashlib.sha256(data).hexdigest()
        record["startup_sha256_before"] = original_hashes
        before = collect_evidence(LAB_DIR, run_dir / "before")
        before_metrics = metrics_from_evidence(layout, before)
        record["phases"]["before"] = {"metrics": before_metrics, "healthy": healthy(before_metrics)}
        if not healthy(before_metrics):
            raise RuntimeError("Current lab is not healthy; no router restart performed")
        record["events"].append("重启前双向邻居 FULL、远端 /32 路由与两类 ping 均正常。")

        print("Preflight and current baseline: healthy. Restarting only r1 and r2...", flush=True)
        record["first_restart"] = restart(layout)
        record["events"].append("按节点名称重启 r1、r2，未重建实验。")
        print("First restart finished. Waiting up to 90 seconds for OSPF recovery...", flush=True)
        first_poll = wait_healthy(layout, before["discovered"], run_dir / "first-poll.json")
        after = collect_evidence(LAB_DIR, run_dir / "after-first-restart")
        first_metrics = metrics_from_evidence(layout, after)
        record["phases"]["after_first_restart"] = {
            "poll_healthy": first_poll, "metrics": first_metrics, "healthy": healthy(first_metrics)}
        if first_poll and healthy(first_metrics):
            record["status"] = "PASS"
            record["events"].append("第一次重启后网络自行收敛，未修改启动文件。")
        else:
            record["events"].append("第一次重启后未通过；保留快照，核对旧配置特征。")
            config_guard(layout, before, after, original_hashes)
            print("Confirmed stale generated FRR configuration. Backing up and repairing its two files...", flush=True)
            for node in layout.nodes:
                paths = startup_files(layout, node)
                host_command(["sudo", "-n", "cp", "--", str(paths["source"]), str(paths["deployed"])])
                if sha(paths["source"]) != sha(paths["deployed"]):
                    raise RuntimeError(f"{node}: copied startup config hash mismatch")
            record["events"].append("已在保存原件后，仅将本项目两份正确源配置复制到各自实际挂载的 frr.conf。")
            record["repair_restart"] = restart(layout)
            record["events"].append("修复后第二次仅重启 r1、r2。")
            print("Second restart finished. Waiting up to 90 seconds for OSPF recovery...", flush=True)
            repair_poll = wait_healthy(layout, before["discovered"], run_dir / "repair-poll.json")
            repaired = collect_evidence(LAB_DIR, run_dir / "after-repair-restart")
            repaired_metrics = metrics_from_evidence(layout, repaired)
            record["phases"]["after_repair_restart"] = {
                "poll_healthy": repair_poll, "metrics": repaired_metrics,
                "healthy": healthy(repaired_metrics)}
            record["status"] = "PASS" if repair_poll and healthy(repaired_metrics) else "FAIL"
            if record["status"] == "PASS":
                record["events"].append("修复后两端邻居 FULL、远端 /32 路由和双向 Loopback ping 均恢复。")
            else:
                record["errors"].append("修复后的最终网络验收失败；请按报告核对现场。")
    except BaseException as exc:
        record["status"] = "FAIL"
        record["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        record["finished_at_utc"] = utc_now()
        write_json(run_dir / "restart-record.json", record)
        render(record, run_dir)
    return run_dir, record


def main() -> int:
    run_dir, record = run()
    print(f"Restart verification: {run_dir}")
    print(f"Status: {record['status']}")
    print(f"Report: {run_dir / 'report.md'}")
    for error in record["errors"]:
        print(error, file=sys.stderr)
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
