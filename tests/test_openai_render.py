import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nano_banana_render import openai_render, worker


class OpenAIRenderTests(unittest.TestCase):
    def test_images_api_edits_the_capture_with_configured_model(self):
        png = b"\x89PNG\r\n\x1a\n" + b"fixture"
        encoded = base64.b64encode(png).decode()
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "view.png"
            capture.write_bytes(png)
            with patch.dict("os.environ", OPENAI_API_KEY="fixture"), patch.object(
                openai_render, "urlopen",
                return_value=BytesIO(json.dumps({"data": [{"b64_json": encoded}]}).encode())
            ) as send:
                result = openai_render.api_edit({"image_model": "chatgpt-image-latest"}, "tecido", capture)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/images/edits")
        self.assertIn(png, request.data)
        self.assertIn(b"chatgpt-image-latest", request.data)
        self.assertEqual(result, encoded)

    def test_codex_failure_does_not_trigger_api_or_repeated_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            capture = Path(folder) / "view.png"
            capture.write_bytes(b"fixture")
            with patch.object(worker, "codex_run", side_effect=ValueError("quota")) as cli, patch.object(worker, "api_edit") as api:
                result = worker.process(dict(provider="codex", settings={}, action="render",
                                             prompt="luz", capture=str(capture), count=4))
        cli.assert_called_once()
        api.assert_not_called()
        self.assertEqual(result["renders"], [{"success": False, "error": "quota"}])
