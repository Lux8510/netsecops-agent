"""No-network tests for project-local API-key loading."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))
from agent_config import AgentConfigError, configuration_status, get_deepseek_api_key  # noqa: E402


class AgentConfigTest(unittest.TestCase):
    def test_empty_placeholder_means_unconfigured(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
            self.assertIsNone(get_deepseek_api_key(path=path, environ={}))
            self.assertFalse(configuration_status(path=path, environ={})["configured"])

    def test_project_env_is_loaded_and_not_reported(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY='test-secret'\n", encoding="utf-8")
            self.assertEqual(get_deepseek_api_key(path=path, environ={}), "test-secret")
            self.assertNotIn("test-secret", str(configuration_status(path=path, environ={})))

    def test_process_environment_takes_precedence(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("DEEPSEEK_API_KEY=local-secret\n", encoding="utf-8")
            self.assertEqual(get_deepseek_api_key(path=path, environ={"DEEPSEEK_API_KEY": "env-secret"}),
                             "env-secret")
            self.assertEqual(configuration_status(path=path, environ={"DEEPSEEK_API_KEY": "env-secret"})["source"],
                             "process_environment")

    def test_invalid_syntax_does_not_echo_line(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("secret-without-equals\n", encoding="utf-8")
            with self.assertRaises(AgentConfigError) as found:
                get_deepseek_api_key(path=path, environ={})
            self.assertNotIn("secret-without-equals", str(found.exception))


if __name__ == "__main__":
    unittest.main()
