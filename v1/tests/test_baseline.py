import subprocess
import unittest
from unittest.mock import patch
from v1.baseline import command, collect

class BaselineTests(unittest.TestCase):
    def test_command_timeout_is_not_success(self):
        with patch('v1.baseline.subprocess.run', side_effect=subprocess.TimeoutExpired(['docker'],1)):
            self.assertIsNone(command(['docker'])['exit_code'])

    def test_missing_docker_cannot_confirm_path(self):
        with patch('v1.baseline.subprocess.run', side_effect=FileNotFoundError('docker')):
            result = collect()
        self.assertEqual(result['status'],'collection_failure')
        self.assertIsNone(result['actual_primary_path'])
        self.assertEqual(len({e['evidence_id'] for e in result['evidence']}),len(result['evidence']))

    def test_successful_command_with_empty_output_cannot_pass(self):
        with patch('v1.baseline.subprocess.run', return_value=subprocess.CompletedProcess([],0,'','')):
            result = collect()
        self.assertEqual(result['status'],'fail')
        self.assertIsNone(result['actual_primary_path'])
