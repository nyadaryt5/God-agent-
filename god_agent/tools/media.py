"""Vision, image generation, and speech.

These are the modalities a general-purpose agent has that a shell does not:
it can *look* at a screenshot, *draw* a diagram, and *say* an alert out loud.

They are built on the same OpenAI-compatible endpoint God-Agent already uses
for reasoning, so there is nothing new to install and no new dependency:

  vision  POST {base_url}/chat/completions   (multimodal message with image)
  image   POST {base_url}/images/generations
  speech  POST {base_url}/audio/speech

If your provider does not implement one of those surfaces, the corresponding
tool returns a clear "not supported by this provider" error instead of failing
mysteriously. Set `media.base_url` / `media.api_key` to point these at a
different endpoint than the one used for chat.

The vision tool is what makes `browser_screenshot` useful: screenshot a page,
then ask what is on it. Without it the agent can capture an image but cannot
read one.

Privacy note: `image_analyze` uploads the image to the configured endpoint.
For a screenshot of a dashboard containing credentials, that means sending it
to a third party. It is off unless you call it, it is audited, and it honours
`policy.network.enabled`.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from ..runtime import get_runtime

MIME_FALLBACK = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}

# Best-effort local playback. Nothing here is executed through a shell.
PLAYERS = ("paplay", "aplay", "ffplay", "mpg123", "mpv", "cvlc", "play")


class MediaError(RuntimeError):
    pass


def _cfg(rt) -> dict:
    m = rt.cfg.get("media") or {}
    llm = rt.cfg.get("llm") or {}
    root = os.path.expanduser(rt.cfg.get("state", {}).get("root", "~/.god-agent"))
    out_dir = m.get("output_dir") or os.path.join(root, "media")
    return {
        "enabled": bool(m.get("enabled", True)),
        "base_url": (m.get("base_url") or llm.get("base_url") or "").rstrip("/"),
        "api_key": m.get("api_key") or "",
        "vision_model": m.get("vision_model") or llm.get("model") or "",
        "image_model": m.get("image_model") or "gpt-image-1",
        "image_size": m.get("image_size") or "1024x1024",
        "speech_model": m.get("speech_model") or "tts-1",
        "speech_voice": m.get("speech_voice") or "alloy",
        "speech_format": m.get("speech_format") or "mp3",
        "output_dir": os.path.expanduser(out_dir),
        "max_image_mb": int(m.get("max_image_mb") or 20),
        "play_audio": bool(m.get("play_audio", False)),
        "max_prompt_chars": int(m.get("max_prompt_chars") or 4000),
        "timeout_s": int(m.get("timeout_s") or 180),
    }


def _api_key(conf: dict) -> str:
    if conf["api_key"]:
        return conf["api_key"]
    try:
        from ..config import api_key as _cfg_key
        from ..runtime import get_runtime as _rt

        return _cfg_key(_rt().cfg) or ""
    except Exception:
        return ""


def _post(conf: dict, path: str, payload: dict) -> bytes:
    """POST JSON to the media endpoint. Returns raw response bytes."""
    base = conf["base_url"]
    key = _api_key(conf)
    if not base:
        raise MediaError("no endpoint configured: set media.base_url (or llm.base_url)")
    if not key:
        raise MediaError("no API key configured (set media.api_key, or llm.api_key_env)")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        f"{base}/{path.lstrip('/')}",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=conf["timeout_s"]) as resp:  # noqa: S310
                return resp.read(50_000_000)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if key:
                detail = detail.replace(key, "***")
            if e.code in (404, 405):
                raise MediaError(
                    f"this provider does not implement /{path.lstrip('/')} (HTTP {e.code})"
                ) from None
            last = MediaError(f"HTTP {e.code}: {detail}")
            if e.code in (400, 401, 403, 422):
                break
            time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = MediaError(f"network error: {e}")
            time.sleep(1.5 * (attempt + 1))
    raise last if last else MediaError("unknown media error")


def _out_path(conf: dict, prefix: str, ext: str) -> str:
    os.makedirs(conf["output_dir"], exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.join(conf["output_dir"], f"{prefix}-{stamp}.{ext.lstrip('.')}")


def _err(rt, action: str, exc: BaseException) -> str:
    msg = str(exc) if isinstance(exc, MediaError) else f"{type(exc).__name__}: {exc}"
    try:
        rt.record(action, f"ERROR: {msg}", {"error": type(exc).__name__})
    except Exception:
        pass
    return f"ERROR: {msg}"


def _guard(rt, conf: dict, action: str) -> Optional[str]:
    if not conf["enabled"]:
        return f"ERROR: media tools are disabled (set media.enabled=true)"
    return None


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------
def image_analyze(reg, name: str, args: dict) -> str:
    """Describe or interpret an image — including browser screenshots.

    Screenshot a page with `browser_screenshot`, then read it with this. Also
    works on diagrams, charts, error dialogs, and photos of a screen.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    if bad := _guard(rt, conf, "image_analyze"):
        return bad

    path = os.path.expanduser(str(args.get("path", "")).strip())
    question = str(args.get("question", "") or "Describe this image in detail.")
    if not path:
        return "ERROR: path required"
    if not os.path.isfile(path):
        return f"ERROR: no such file: {path}"

    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > conf["max_image_mb"]:
        return (f"ERROR: image is {size_mb:.1f}MB, over the "
                f"{conf['max_image_mb']}MB cap (media.max_image_mb)")

    ext = os.path.splitext(path)[1].lower()
    mime = MIME_FALLBACK.get(ext) or mimetypes.guess_type(path)[0] or "image/png"
    try:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
    except OSError as e:
        return _err(rt, "image_analyze", MediaError(f"cannot read {path}: {e}"))

    payload = {
        "model": conf["vision_model"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": question[: conf["max_prompt_chars"]]},
                {"type": "image_url",
                 "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        "max_tokens": 1024,
    }
    try:
        raw = _post(conf, "/chat/completions", payload)
        data = json.loads(raw)
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    except (ValueError, KeyError, IndexError) as e:
        return _err(rt, "image_analyze", MediaError(f"unexpected response: {e}"))
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "image_analyze", exc)

    text = text.strip() or "(model returned no description)"
    rt.record("image_analyze", f"analyzed {path} ({size_mb:.1f}MB)",
              {"path": path, "mime": mime, "chars": len(text)})
    return text


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------
def image_generate(reg, name: str, args: dict) -> str:
    """Generate an image from a text prompt and save it to disk.

    Useful for architecture diagrams, network topologies, and illustrative
    graphics. Returns the saved file path.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    if bad := _guard(rt, conf, "image_generate"):
        return bad

    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "ERROR: prompt required"
    prompt = prompt[: conf["max_prompt_chars"]]
    size = str(args.get("size") or conf["image_size"])

    payload = {
        "model": conf["image_model"],
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": "b64_json",
    }
    try:
        raw = _post(conf, "/images/generations", payload)
        data = json.loads(raw)
        item = (data.get("data") or [{}])[0]
    except (ValueError, KeyError, IndexError) as e:
        return _err(rt, "image_generate", MediaError(f"unexpected response: {e}"))
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "image_generate", exc)

    try:
        if item.get("b64_json"):
            out = _out_path(conf, "image", "png")
            with open(out, "wb") as fh:
                fh.write(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            out = _out_path(conf, "image", "png")
            with urllib.request.urlopen(item["url"], timeout=conf["timeout_s"]) as resp:  # noqa: S310
                with open(out, "wb") as fh:
                    fh.write(resp.read(50_000_000))
        else:
            return _err(rt, "image_generate",
                        MediaError("provider returned neither b64_json nor url"))
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "image_generate", exc)

    rev = item.get("revised_prompt") or ""
    rt.record("image_generate", f"generated image -> {out}",
              {"path": out, "size": size, "model": conf["image_model"],
               "prompt": prompt[:200]})
    return f"ok: saved {out}" + (f"\nrevised prompt: {rev}" if rev else "")


# ---------------------------------------------------------------------------
# Speech
# ---------------------------------------------------------------------------
def _play(path: str) -> str:
    """Best-effort local playback. Never fatal, never uses a shell."""
    for player in PLAYERS:
        exe = shutil.which(player)
        if not exe:
            continue
        try:
            subprocess.run(  # noqa: S603
                [exe, "-q", path] if player in ("mpv",) else [exe, path],
                timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            return f"played via {player}"
        except Exception:
            continue
    return "no audio player found (tried: " + ", ".join(PLAYERS) + ")"


def speak(reg, name: str, args: dict) -> str:
    """Convert text to speech and save it as an audio file.

    Use it for audible alerts — a monitoring agent that says "disk critical on
    db-01" is more useful than one that writes another log line. Set
    `media.play_audio=true` to also play it on the host's default output.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    if bad := _guard(rt, conf, "speak"):
        return bad

    text = str(args.get("text", "")).strip()
    if not text:
        return "ERROR: text required"
    text = text[: conf["max_prompt_chars"]]
    voice = str(args.get("voice") or conf["speech_voice"])
    fmt = str(args.get("format") or conf["speech_format"]).lower()
    if fmt not in ("mp3", "opus", "aac", "flac", "wav", "pcm"):
        return "ERROR: format must be one of mp3|opus|aac|flac|wav|pcm"

    payload = {
        "model": conf["speech_model"],
        "input": text,
        "voice": voice,
        "response_format": fmt,
    }
    try:
        audio = _post(conf, "/audio/speech", payload)
        if not audio:
            raise MediaError("provider returned empty audio")
        out = _out_path(conf, "speech", fmt)
        with open(out, "wb") as fh:
            fh.write(audio)
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "speak", exc)

    played = ""
    if conf["play_audio"] or args.get("play"):
        played = " [" + _play(out) + "]"
    rt.record("speak", f"synthesized {len(text)} chars -> {out}",
              {"path": out, "voice": voice, "format": fmt, "chars": len(text)})
    return f"ok: saved {out} ({len(audio)} bytes, voice={voice}){played}"


__all__ = ["image_analyze", "image_generate", "speak", "MediaError"]
