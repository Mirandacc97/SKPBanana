"""ChatGPT-authenticated Codex CLI and opt-in OpenAI Images API adapters."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import sys
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
        return _run_in_folder(folder, args, instruction, prompt, capture)


def _run_in_folder(folder, args, instruction, prompt, capture):
    event_path = Path(folder) / "events.jsonl"
    with event_path.open("wb") as events_file, event_path.open("rb") as reader, tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(args + ["-"], cwd=folder, stdin=subprocess.PIPE, stdout=events_file,
                                stderr=errors, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # Read through a separate file handle: seeking the child's stdout handle
        # while it is writing would also move its write position on Windows.
        events = []
        deadline = time.monotonic() + 600
        thread = None
        try:
            proc.stdin.write((instruction + "\nDo not run commands. Design brief:\n" + prompt).encode())
            proc.stdin.close()
            while True:
                done = proc.poll() is not None
                for line in reader:
                    if not line.endswith(b"\n") and not done:
                        reader.seek(-len(line), 1)
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    events.append(event)
                    if event.get("type") == "thread.started":
                        thread = event.get("thread_id")
                        if thread and re.fullmatch(r"[0-9a-fA-F-]{36}", thread):
                            print(f"codex session={thread} pid={proc.pid}", file=sys.stderr, flush=True)
                if capture and thread and re.fullmatch(r"[0-9a-fA-F-]{36}", thread):
                    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
                    directory = home / "generated_images" / thread
                    files = list(directory.glob("*.png"))
                    if len(files) == 1 and files[0].resolve().parent == directory.resolve():
                        try:
                            image = files[0].read_bytes()
                        except PermissionError:
                            image = b""
                        if (image.startswith(b"\x89PNG\r\n\x1a\n") and image.endswith(b"IEND\xaeB`\x82")
                                and image != Path(capture).read_bytes()):
                            print(f"codex image ready session={thread} bytes={len(image)}", file=sys.stderr, flush=True)
                            return base64.b64encode(image).decode("ascii")
                if done:
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("Codex timed out after 10 minutes.")
                time.sleep(0.25)
        finally:
            proc.stdin.close()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
        if proc.returncode or not any(e.get("type") == "turn.completed" for e in events):
            errors.seek(0)
            diagnostics = errors.read().lower() + json.dumps([e for e in events if e.get("type") in
                {"error", "turn.failed"}]).lower().encode()
            print(f"codex exit={proc.returncode} stderr_bytes={errors.tell()} events={len(events)}",
                  file=sys.stderr, flush=True)
            cause = next((label for word, label in [(b"401", "authentication"), (b"login", "authentication"),
                         (b"429", "rate limit"), (b"quota", "quota"), (b"model", "model access"),
                         (b"connect", "connection")] if word in diagnostics), "no completed turn")
            raise ValueError(f"Codex exit {proc.returncode}: {cause}.")
    if not capture:
        messages = [e["item"]["text"] for e in events if e.get("type") == "item.completed"
                    and e.get("item", {}).get("type") == "agent_message"]
        if not messages:
            raise ValueError("Codex returned no prompt.")
        return messages[-1]
    raise ValueError("Codex finished without a complete PNG for this session.")


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
