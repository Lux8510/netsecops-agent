#!/usr/bin/env python3
"""Read-only Stage 5 FRR version and interconnect command preflight."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from collect_evidence import LAB_DIR, ensure_wsl2_and_sudo, read_layout, utc_now
from run_area_mismatch import preflight


def main() -> int:
    ensure_wsl2_and_sudo()
    layout = read_layout(LAB_DIR)
    preflight(layout)
    output = {"at_utc": utc_now(), "nodes": {}}
    for node in layout.nodes:
        interface = layout.interfaces[node]
        commands = ("show version", f"show interface {interface}",
                    f"show ip ospf interface {interface}", "show running-config")
        output["nodes"][node] = {}
        for command in commands:
            argv = ["sudo", "-n", "docker", "exec", layout.container(node),
                    "vtysh", "-c", command]
            done = subprocess.run(argv, capture_output=True, text=True,
                                  errors="replace", timeout=15, check=False)
            output["nodes"][node][command] = {
                "command": argv, "exit_code": done.returncode,
                "stdout": done.stdout, "stderr": done.stderr,
            }
    directory = LAB_DIR / "stage5-preflight"
    directory.mkdir(exist_ok=True)
    path = directory / "frr-capabilities.json"
    if path.exists():
        raise RuntimeError(f"Refusing to overwrite existing preflight: {path}")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for node, records in output["nodes"].items():
        version = records["show version"]
        line = next((line for line in version["stdout"].splitlines() if "FRRouting" in line), "not reported")
        print(f"{node}: {line}; version query exit={version['exit_code']}")
        for command, record in records.items():
            if record["exit_code"] != 0:
                print(f"{node}: {command}: exit={record['exit_code']}")
    print(f"Preflight: {path}")
    return 0 if all(record["exit_code"] == 0 for records in output["nodes"].values()
                    for record in records.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
