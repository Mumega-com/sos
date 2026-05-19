import io
import json
import unittest
from contextlib import redirect_stdout

from sos.observability.logging import SOSLogger, clear_context
from sos.observability.tracing import TraceSpan


class TestObservabilityIntegration(unittest.TestCase):
    def test_trace_span_ids_appear_in_logs(self):
        clear_context()
        logger = SOSLogger("test", min_level="info")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            with TraceSpan("test-span"):
                logger.info("hello")

        line = buffer.getvalue().strip().splitlines()[-1]
        record = json.loads(line)

        self.assertEqual(record["service"], "test")
        self.assertEqual(record["msg"], "hello")
        self.assertIn("trace_id", record)
        self.assertIn("span_id", record)


if __name__ == "__main__":
    unittest.main()

