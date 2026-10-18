"""Local, no-container checks for discovery and safe restoration decisions."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main, mock


LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

import collect_evidence as collector  # noqa: E402
import run_area_mismatch as experiment  # noqa: E402


class Phase2Tests(TestCase):
    def setUp(self):
        self.layout = collector.read_layout(LAB_DIR)

    def test_actual_topology_and_configs_are_discovered(self):
        self.assertEqual(self.layout.nodes, ("r1", "r2"))
        for node in self.layout.nodes:
            config = (LAB_DIR / "configs" / node / "frr.conf").read_text(encoding="utf-8")
            details = collector.parse_node_config(config, self.layout.interfaces[node])
            self.assertEqual(details["link_area"], "0.0.0.0")
            self.assertEqual(details["ospf_mode"], "network")
            self.assertEqual(details["link_prefix"], "10.0.12.0/30")
            self.assertEqual(details["loopback_prefix"], f"10.255.0.{1 if node == 'r1' else 2}/32")

    def test_old_generated_startup_config_is_observable(self):
        for node in self.layout.nodes:
            old_config = (Path(__file__).resolve().parent / f"fixtures/configs/{node}-startup-frr.conf").read_text(encoding="utf-8")
            details = collector.parse_node_config(old_config, self.layout.interfaces[node])
            self.assertEqual(details["ospf_mode"], "interface")
            self.assertEqual(details["link_area"], "0.0.0.0")

    def test_collector_rejects_free_form_commands(self):
        with self.assertRaises(ValueError):
            collector.command_argv(self.layout, "r1", "sh -c anything", {})
        with self.assertRaises(ValueError):
            collector.checked_name("eth1; whoami")

    def test_partial_injection_restores_original_network(self):
        before = (LAB_DIR / "configs" / "r1" / "frr.conf").read_text(encoding="utf-8")
        partial = before.replace(" network 10.0.12.0/30 area 0.0.0.0\n", "")
        with mock.patch.object(experiment, "running_config", side_effect=[partial, before]), \
             mock.patch.object(experiment, "configure_network", return_value={"exit_code": 0}) as configure:
            result = experiment.restore_original(
                self.layout, "r1", "10.0.12.0/30", "0.0.0.0", "0.0.0.1"
            )
        configure.assert_called_once_with(
            self.layout, "r1", ["network 10.0.12.0/30 area 0.0.0.0"]
        )
        self.assertEqual(result["config_after"], before)

    def test_partial_injection_restores_original_interface_area(self):
        before = (Path(__file__).resolve().parent / "fixtures/configs/r1-startup-frr.conf").read_text(encoding="utf-8")
        partial = before.replace(" ip ospf area 0.0.0.0\n", "", 1)
        with mock.patch.object(experiment, "running_config", side_effect=[partial, before]), \
             mock.patch.object(experiment, "configure_interface_area", return_value={"exit_code": 0}) as configure:
            result = experiment.restore_original(
                self.layout, "r1", "10.0.12.0/30", "0.0.0.0", "0.0.0.1", "interface", "eth1"
            )
        configure.assert_called_once_with(self.layout, "r1", "eth1", ["ip ospf area 0.0.0.0"])
        self.assertEqual(result["config_after"], before)

    def test_interface_area_command_is_restricted(self):
        with self.assertRaises(experiment.ExperimentError):
            experiment.configure_interface_area(self.layout, "r1", "eth2", ["ip ospf area 0.0.0.1"])
        with self.assertRaises(experiment.ExperimentError):
            experiment.configure_interface_area(self.layout, "r1", "eth1", ["write memory"])
        with mock.patch.object(experiment, "host_command", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as host:
            experiment.configure_interface_area(self.layout, "r1", "eth1", ["no ip ospf area 0.0.0.0", "ip ospf area 0.0.0.1"])
        argv = host.call_args.args[0]
        self.assertEqual(argv[0:5], ["sudo", "-n", "docker", "exec", "clab-netsecops-ospf-r1"])
        self.assertNotIn("write memory", argv)

    def test_neighbor_and_route_predicates(self):
        self.assertTrue(experiment.full_neighbor(
            "10.255.0.2 1 Full/- 6s 33s 10.0.12.2 eth1:10.0.12.1", "10.255.0.2"
        ))
        self.assertFalse(experiment.full_neighbor("", "10.255.0.2"))
        self.assertTrue(experiment.learned_route(
            "O>* 10.255.0.2/32 [110/10] via 10.0.12.2, eth1", "10.255.0.2/32"
        ))
        self.assertFalse(experiment.learned_route("C>* 10.255.0.2/32 is directly connected", "10.255.0.2/32"))

    def test_injection_exception_still_attempts_restore_and_recovery_capture(self):
        original = (LAB_DIR / "configs" / "r1" / "frr.conf").read_text(encoding="utf-8")
        details = {
            node: collector.parse_node_config(
                (LAB_DIR / "configs" / node / "frr.conf").read_text(encoding="utf-8"),
                self.layout.interfaces[node],
            ) for node in self.layout.nodes
        }
        good = {node: {"neighbor_full": True, "remote_loopback_route": True,
                       "peer_link_ping": True, "remote_loopback_ping": True,
                       "link_area": "0.0.0.0"} for node in self.layout.nodes}

        def fake_collect(_lab_dir, path):
            path.mkdir(parents=True, exist_ok=False)
            return {"discovered": details, "stage": path.parts[-1]}

        with TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "source.conf"
            deployed = temp / "deployed.conf"
            source.write_text(original, encoding="utf-8")
            deployed.write_text(original, encoding="utf-8")
            with mock.patch.object(experiment, "ensure_wsl2_and_sudo"), \
                 mock.patch.object(experiment, "preflight", return_value={}), \
                 mock.patch.object(experiment, "collect_evidence", side_effect=fake_collect), \
                 mock.patch.object(experiment, "metrics_from_evidence", return_value=good), \
                 mock.patch.object(experiment, "by_id", return_value={"stdout": original}), \
                 mock.patch.object(experiment, "startup_files", return_value={"source": source, "deployed": deployed}), \
                 mock.patch.object(experiment, "configure_network", side_effect=experiment.ExperimentError("injection failed")), \
                 mock.patch.object(experiment, "restore_original", return_value={"action": {}, "config_after": original}) as restore, \
                 mock.patch.object(experiment, "poll_state", return_value=(True, good)):
                run_dir, record = experiment.run_experiment(temp)
            restore.assert_called_once()
            self.assertEqual(record["status"], "FAIL")
            self.assertTrue((run_dir / "observations" / "recovered" / "snapshot").exists())
            self.assertIn("手动恢复", (run_dir / "comparison.md").read_text(encoding="utf-8"))

    def test_complete_control_flow_writes_three_distinct_evidence_groups(self):
        original = (LAB_DIR / "configs" / "r1" / "frr.conf").read_text(encoding="utf-8")
        faulty = original.replace("network 10.0.12.0/30 area 0.0.0.0",
                                  "network 10.0.12.0/30 area 0.0.0.1")
        details = {
            node: collector.parse_node_config(
                (LAB_DIR / "configs" / node / "frr.conf").read_text(encoding="utf-8"),
                self.layout.interfaces[node],
            ) for node in self.layout.nodes
        }
        good = {node: {"neighbor_full": True, "remote_loopback_route": True,
                       "peer_link_ping": True, "remote_loopback_ping": True,
                       "link_area": "0.0.0.0"} for node in self.layout.nodes}
        bad = {node: {"neighbor_full": False, "remote_loopback_route": False,
                      "peer_link_ping": True, "remote_loopback_ping": False,
                      "link_area": "0.0.0.1" if node == "r1" else "0.0.0.0"}
               for node in self.layout.nodes}

        def fake_collect(_lab_dir, path):
            path.mkdir(parents=True, exist_ok=False)
            stage = path.parent.name if path.name == "snapshot" else path.name
            return {"discovered": details, "stage": stage}

        with TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "source.conf"
            deployed = temp / "deployed.conf"
            source.write_text(original, encoding="utf-8")
            deployed.write_text(original, encoding="utf-8")
            with mock.patch.object(experiment, "ensure_wsl2_and_sudo"), \
                 mock.patch.object(experiment, "preflight", return_value={}), \
                 mock.patch.object(experiment, "collect_evidence", side_effect=fake_collect), \
                 mock.patch.object(experiment, "metrics_from_evidence", side_effect=lambda _layout, evidence: bad if evidence["stage"] == "fault" else good), \
                 mock.patch.object(experiment, "by_id", return_value={"stdout": original}), \
                 mock.patch.object(experiment, "startup_files", return_value={"source": source, "deployed": deployed}), \
                 mock.patch.object(experiment, "configure_network", return_value={"exit_code": 0}), \
                 mock.patch.object(experiment, "running_config", return_value=faulty), \
                 mock.patch.object(experiment, "restore_original", return_value={"action": {}, "config_after": original}), \
                 mock.patch.object(experiment, "poll_state", side_effect=[(True, bad), (True, good)]):
                run_dir, record = experiment.run_experiment(temp)
            self.assertEqual(record["status"], "PASS", record["errors"])
            for stage in ("normal", "fault", "recovered"):
                self.assertTrue((run_dir / "observations" / stage).exists())
            self.assertTrue((run_dir / "comparison.md").exists())

    def test_stable_fault_agent_error_still_restores_and_checks_recovery(self):
        original = (LAB_DIR / "configs" / "r1" / "frr.conf").read_text(encoding="utf-8")
        faulty = original.replace("network 10.0.12.0/30 area 0.0.0.0",
                                  "network 10.0.12.0/30 area 0.0.0.1")
        details = {node: collector.parse_node_config(
            (LAB_DIR / "configs" / node / "frr.conf").read_text(encoding="utf-8"), self.layout.interfaces[node])
            for node in self.layout.nodes}
        good = {node: {"neighbor_full": True, "remote_loopback_route": True,
                       "peer_link_ping": True, "remote_loopback_ping": True, "link_area": "0.0.0.0"}
                for node in self.layout.nodes}
        bad = {node: {"neighbor_full": False, "remote_loopback_route": False,
                      "peer_link_ping": True, "remote_loopback_ping": False,
                      "link_area": "0.0.0.1" if node == "r1" else "0.0.0.0"}
               for node in self.layout.nodes}

        def fake_collect(_lab_dir, path):
            path.mkdir(parents=True, exist_ok=False)
            stage = path.parent.name if path.name == "snapshot" else path.name
            return {"discovered": details, "stage": stage}

        with TemporaryDirectory() as directory:
            temp = Path(directory)
            source, deployed = temp / "source.conf", temp / "deployed.conf"
            source.write_text(original, encoding="utf-8")
            deployed.write_text(original, encoding="utf-8")
            hook = mock.Mock(side_effect=RuntimeError("agent failed"))
            with mock.patch.object(experiment, "ensure_wsl2_and_sudo"), \
                 mock.patch.object(experiment, "preflight", return_value={}), \
                 mock.patch.object(experiment, "collect_evidence", side_effect=fake_collect), \
                 mock.patch.object(experiment, "metrics_from_evidence", side_effect=lambda _layout, evidence: bad if evidence["stage"] == "fault" else good), \
                 mock.patch.object(experiment, "by_id", return_value={"stdout": original}), \
                 mock.patch.object(experiment, "startup_files", return_value={"source": source, "deployed": deployed}), \
                 mock.patch.object(experiment, "configure_network", return_value={"exit_code": 0}), \
                 mock.patch.object(experiment, "running_config", return_value=faulty), \
                 mock.patch.object(experiment, "restore_original", return_value={"action": {}, "config_after": original}) as restore, \
                 mock.patch.object(experiment, "poll_state", side_effect=[(True, bad), (True, good)]):
                run_dir, record = experiment.run_experiment(temp, on_fault_stable=hook)
            hook.assert_called_once_with()
            restore.assert_called_once()
            self.assertEqual(record["status"], "FAIL")
            self.assertTrue(record["phases"]["recovered"]["passed"])
            self.assertTrue((run_dir / "observations" / "recovered" / "snapshot").exists())


if __name__ == "__main__":
    main()
