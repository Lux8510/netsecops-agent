"""Disposable, fully mocked experiment child for the SIGTERM regression test."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import time
from unittest import mock

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
import run_fault_scenario  # noqa: E402


def main(output: Path) -> None:
    normal = json.loads((LAB / "tests/fixtures/observations/normal.json").read_text(encoding="utf-8"))
    original = next(item["stdout"] for item in normal["nodes"]["r1"]["commands"]
                    if item["id"] == "running_config")
    good = {node: {"neighbor_full": True, "remote_loopback_route": True,
                   "peer_link_ping": True, "remote_loopback_ping": True,
                   "link_area": "0.0.0.0"} for node in ("r1", "r2")}
    startup = output / "startup.conf"
    startup.write_text(original, encoding="utf-8")

    def collect(_lab: Path, destination: Path) -> dict:
        destination.mkdir(parents=True, exist_ok=False)
        return deepcopy(normal)

    def configure(_layout, _node, _interface, command):
        (output / ("injected" if command == "shutdown" else "restored")).touch()
        return {"exit_code": 0, "command": []}

    def running(_layout, _node):
        if (output / "restored").exists():
            return original
        return original.replace("interface eth1\n", "interface eth1\n shutdown\n", 1)

    def poll(_layout, _details, _destination, fault, _timeout):
        if fault:
            (output / "ready").touch()
            while True:
                time.sleep(0.01)
        return True, good

    with (mock.patch.object(run_fault_scenario, "ensure_wsl2_and_sudo"),
          mock.patch.object(run_fault_scenario, "preflight", return_value={}),
          mock.patch.object(run_fault_scenario.subprocess, "run") as command,
          mock.patch.object(run_fault_scenario, "collect_evidence", side_effect=collect),
          mock.patch.object(run_fault_scenario, "metrics_from_evidence", return_value=good),
          mock.patch.object(run_fault_scenario, "startup_files", return_value={"source": startup, "deployed": startup}),
          mock.patch.object(run_fault_scenario, "configure_setting", side_effect=configure),
          mock.patch.object(run_fault_scenario, "running_config", side_effect=running),
          mock.patch.object(run_fault_scenario, "poll", side_effect=poll)):
        command.return_value.returncode = 0
        command.return_value.stdout = "FRRouting 10.7.1_git (mock)\n"
        command.return_value.stderr = ""
        _, record = run_fault_scenario.run_experiment("admin_down", "r1", output / "experiments")
        (output / "result.json").write_text(json.dumps({"status": record["status"],
                                                      "recovered": record.get("phases", {}).get("recovered", {}).get("passed")}),
                                            encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
