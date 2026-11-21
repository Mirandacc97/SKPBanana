import base64
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from nano_banana_render.openai_render import _run_in_folder


class ResultDeliveryTests(unittest.TestCase):
    def test_png_ready_before_cli_exits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            capture = root / "original.png"
            capture.write_bytes(b"source")
            session = str(uuid.uuid4())
            output = root / "generated_images" / session / "result.png"
            output.parent.mkdir(parents=True)
            script = """import json, pathlib, sys, time
sys.stdin.read()
print(json.dumps(dict(type='thread.started', thread_id=sys.argv[1])), flush=True)
path = pathlib.Path(sys.argv[2])
path.write_bytes(bytes.fromhex('89504e470d0a1a0a') + bytes(2357159))
time.sleep(0.5)
with path.open('ab') as f:
    f.write(bytes.fromhex('0000000049454e44ae426082'))
time.sleep(30)
"""
            started = time.monotonic()
            with patch.dict(os.environ, CODEX_HOME=temp):
                encoded = _run_in_folder(temp, [sys.executable, "-c", script, session, str(output)],
                                         "edit", "fixture", capture)
            self.assertLess(time.monotonic() - started, 15)
            self.assertEqual(base64.b64decode(encoded), output.read_bytes())

    def test_cli_failure_preserves_status_and_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, "-c", "import sys; sys.stdin.read(); sys.stderr.write('HTTP 429'); sys.exit(7)"]
            with self.assertRaisesRegex(ValueError, "exit 7: rate limit"):
                _run_in_folder(temp, command, "edit", "fixture", None)
