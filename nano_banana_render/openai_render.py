"""ChatGPT-authenticated Codex CLI and opt-in OpenAI Images API adapters."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def codex_run(settings, prompt, capture=None):
    executable = shutil.which(settings["codex_executable"])
    if not executable:
        raise ValueError("Codex CLI unavailable. Set codex_executable in render.conf.")
    if os.name == "nt" and Path(executable).suffix.lower() != ".exe":
        raise ValueError("Set codex_executable to the native codex.exe in render.conf.")
    args = [executable, "exec", "--ignore-user-config", "-c", 'forced_login_method="chatgpt"',
            "-s", "read-only", "--skip-git-repo-check", "--ephemeral", "--json"]
    if settings.get("agent_model"):
        args.extend(["-m", settings["agent_model"]])
    if capture:
        args.extend(["--enable", "image_generation", "-i", str(Path(capture).resolve())])
        instruction = ("Use the native image generation tool once to edit the attached SketchUp "
                       "view. Preserve the camera and architecture. Return its generated PNG path.")
    else:
        instruction = "Rewrite the design brief as a concise rendering prompt in the same language."
    with tempfile.TemporaryDirectory() as folder:
        try:
            run = subprocess.run(args + ["-"], input=instruction + "\nDo not run commands. Design brief:\n" + prompt,
                                 cwd=folder, capture_output=True, encoding="utf-8", errors="replace",
                                 timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise ValueError("Codex timed out after 10 minutes.") from None
    if run.returncode:
        raise ValueError("Codex failed. Check codex login status, model access and usage limits.")
    events = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
    if not any(event.get("type") == "turn.completed" for event in events):
        raise ValueError("Codex did not complete the request.")
    if not capture:
        messages = [e["item"]["text"] for e in events if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") == "agent_message"]
        if not messages:
            raise ValueError("Codex returned no prompt.")
        return messages[-1]
    thread = next((e.get("thread_id", "") for e in events if e.get("type") == "thread.started"), "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", thread):
        raise ValueError("Missing Codex generation session.")
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    directory = home / "generated_images" / thread
    files = list(directory.glob("*.png"))
    if len(files) != 1 or files[0].resolve().parent != directory.resolve():
        raise ValueError("Expected one generated PNG in the Codex session.")
    image = files[0].read_bytes()
    if not image.startswith(b"\x89PNG\r\n\x1a\n") or image == Path(capture).read_bytes():
        raise ValueError("Codex returned an invalid or unchanged image.")
    return base64.b64encode(image).decode("ascii")


def api_edit(settings, prompt, capture):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("Set OPENAI_API_KEY for the OpenAI Images API.")
    boundary = uuid.uuid4().hex
    fields = {"model": settings["image_model"], "prompt": prompt, "output_format": "png"}
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="view.png"\r\n'
                  'Content-Type: image/png\r\n\r\n'.encode(), Path(capture).read_bytes(),
                  f'\r\n--{boundary}--\r\n'.encode()])
    request = Request("https://api.openai.com/v1/images/edits", data=b"".join(parts), headers={
        "Authorization": "Bearer " + key, "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urlopen(request, timeout=300) as response:
            images = json.load(response).get("data") or []
    except HTTPError as exc:
        raise ValueError(f"Images API HTTP {exc.code}. Check credentials, quota and image model.") from None
    except (URLError, TimeoutError):
        raise ValueError("Images API connection failed or timed out.") from None
    encoded = images[0].get("b64_json") if images else None
    if not encoded or not base64.b64decode(encoded, validate=True).startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Images API returned no PNG.")
    return encoded
