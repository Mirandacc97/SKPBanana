import base64
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nano_banana_render import worker


class WorkerTests(unittest.TestCase):
    def test_render_variations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "câmera.png"
            image = b"fixture"
            path.write_bytes(image)
            encoded = base64.b64encode(image).decode()
            def fake(model, key, parts, modes):
                self.assertEqual(parts[1]["inlineData"]["data"], encoded)
                return [{"inlineData": {"mimeType": "image/jpeg", "data": encoded}}]
            job = dict(action="render", prompt="madeira", key="test", capture=str(path),
                       model="test", count=4)
            result = worker.process(job, fake)
            self.assertEqual(result["original"], encoded)
            self.assertEqual(result["renders"], [
                {"success": True, "image": encoded, "mime": "image/jpeg"}] * 4)
            failures = worker.process(job, lambda *args: [])
            self.assertTrue(all(not r["success"] for r in failures["renders"]))

    def test_enhance(self):
        job = dict(action="enhance", prompt="luz suave", key="test")
        self.assertEqual(worker.process(job, lambda *args: [{"text": "Luz difusa."}]), "Luz difusa.")

    def test_process_protocol(self):
        reply = subprocess.run([sys.executable, worker.__file__], input=b"{}",
                               capture_output=True, check=True)
        self.assertIn(b'"error"', reply.stdout)
