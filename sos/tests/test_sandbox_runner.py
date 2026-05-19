import sys
import unittest

from sos.execution import SandboxPolicy, run_subprocess


class TestSandboxRunner(unittest.TestCase):
    def test_run_subprocess_success(self):
        policy = SandboxPolicy(timeout_seconds=2.0)
        result = run_subprocess([sys.executable, "-c", "print('hi')"], policy=policy)
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hi", result.stdout)

    def test_run_subprocess_timeout(self):
        policy = SandboxPolicy(timeout_seconds=0.1)
        result = run_subprocess([sys.executable, "-c", "import time; time.sleep(1)"], policy=policy)
        self.assertFalse(result.success)
        self.assertEqual(result.error, "timeout")


if __name__ == "__main__":
    unittest.main()

