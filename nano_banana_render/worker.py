"""Gemini worker for SketchUp. One UTF-8 JSON request and response per process."""
import base64
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def generate(model, key, parts, modalities):
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", model):
        raise ValueError("Invalid model name")
    payload = {"contents": [{"parts": parts}],
               "generationConfig": {"responseModalities": modalities}}
    request = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "x-goog-api-key": key,
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            body = json.load(response)
    except HTTPError as exc:
        messages = {400: "Invalid request or key", 401: "Authentication failed",
                    403: "Access denied", 404: "Model unavailable", 429: "Quota exceeded"}
        raise ValueError(f"{messages.get(exc.code, 'Provider failure')} (HTTP {exc.code})") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Connection failed or timed out") from None
    candidates = body.get("candidates") or []
    if not candidates:
        raise ValueError("Provider returned no candidates")
    return candidates[0].get("content", {}).get("parts", [])


def process(job, send=generate):
    key = job["key"]
    prompt = job["prompt"].strip()
    if not key or not prompt:
        raise ValueError("Enter an API key and prompt")
    if job["action"] == "enhance":
        instruction = ("Rewrite this interior rendering prompt in the same language. "
                       "Preserve camera, openings and furniture placement. Return only the prompt.\n")
        parts = send("gemini-2.5-flash", key, [{"text": instruction + prompt}], ["TEXT"])
        answer = " ".join(p["text"] for p in parts if p.get("text") and not p.get("thought")).strip()
        if not answer:
            raise ValueError("Provider returned no prompt")
        return answer
    if job["action"] != "render" or job["count"] not in range(1, 5):
        raise ValueError("Invalid render request")
    original = base64.b64encode(Path(job["capture"]).read_bytes()).decode("ascii")
    parts = [{"text": prompt}, {"inlineData": {"mimeType": "image/png", "data": original}}]

    def variation(_):
        try:
            for part in send(job["model"], key, parts, ["TEXT", "IMAGE"]):
                data = part.get("inlineData") or part.get("inline_data") or {}
                mime = data.get("mimeType") or data.get("mime_type")
                if mime in {"image/png", "image/jpeg", "image/webp"} and data.get("data") and not part.get("thought"):
                    base64.b64decode(data["data"], validate=True)
                    return {"success": True, "image": data["data"], "mime": mime}
            raise ValueError("Provider returned no supported image")
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=job["count"]) as pool:
        renders = list(pool.map(variation, range(job["count"])))
    return {"original": original, "renders": renders}


def main():
    try:
        result = {"value": process(json.load(sys.stdin.buffer))}
    except (ValueError, KeyError, OSError) as exc:
        result = {"error": str(exc)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
