"""Image editing: prepare images before they go into a document.

Screenshots arrive the size the browser made them, generated diagrams arrive
at 1024x1024, and a report that mixes both looks like a ransom note. This is
the step between `browser_screenshot` / `image_generate` and `doc_add_image`:
resize, crop, rotate, convert, annotate, and compose.

Pillow is required (already an optional dependency of the `gui` extra, so many
installs have it). When it is missing the tools return an install hint instead
of failing at startup, and `dependencies` stays `[]`.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from ..runtime import get_runtime

INSTALL_HINT = ("image editing needs Pillow: pip install 'god-agent[docs]' "
                "(or: pip install pillow)")


class ImageError(RuntimeError):
    pass


def _cfg(rt) -> dict:
    i = rt.cfg.get("imaging") or {}
    return {
        "enabled": bool(i.get("enabled", True)),
        "max_image_mb": int(i.get("max_image_mb") or 25),
        "jpeg_quality": max(1, min(int(i.get("jpeg_quality") or 90), 100)),
        "default_format": str(i.get("default_format") or "png").upper(),
    }


def _err(rt, action: str, exc: BaseException) -> str:
    msg = str(exc) if isinstance(exc, ImageError) else f"{type(exc).__name__}: {exc}"
    try:
        rt.record(action, f"ERROR: {msg}", {"error": type(exc).__name__})
    except Exception:
        pass
    return f"ERROR: {msg}"


def _guard(rt, conf: dict) -> Optional[str]:
    if not conf["enabled"]:
        return "ERROR: image tools are disabled (set imaging.enabled=true)"
    return None


def _open(path: str, max_mb: int):
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError as exc:
        raise ImageError(INSTALL_HINT) from exc
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ImageError(f"no such image: {path}")
    if os.path.getsize(path) / (1024 * 1024) > max_mb:
        raise ImageError(f"image is larger than the {max_mb}MB cap "
                         f"(imaging.max_image_mb)")
    try:
        return Image.open(path)
    except Exception as e:  # noqa: BLE001
        raise ImageError(f"cannot open {path}: {e}") from None


def _resolve_out(src: str, out: Optional[str], default_suffix: str) -> str:
    if out:
        return os.path.expanduser(str(out))
    stem = os.path.splitext(os.path.expanduser(str(src)))[0]
    return f"{stem}{default_suffix}"


def _save_img(img, out: str, fmt: str, quality: int) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    target = out
    # Pillow refuses RGBA for JPEG; flatten rather than crash.
    if fmt.upper() in ("JPEG", "JPG") and img.mode in ("RGBA", "LA", "P"):
        bg = None
        try:
            from PIL import Image as _I  # noqa: PLC0415

            bg = _I.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        except Exception:
            img = img.convert("RGB")
    save_kwargs: dict[str, Any] = {}
    if fmt.upper() in ("JPEG", "JPG"):
        save_kwargs["quality"] = quality
    img.save(target, format=fmt, **save_kwargs)
    return os.path.getsize(target) if os.path.isfile(target) else 0


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def image_info(reg, name: str, args: dict) -> str:
    """Report an image's dimensions, format, mode, and file size."""
    rt = get_runtime()
    conf = _cfg(rt)
    if bad := _guard(rt, conf):
        return bad
    try:
        img = _open(str(args.get("path", "")), conf["max_image_mb"])
        path = os.path.expanduser(str(args.get("path", "")))
        with img:
            lines = [
                f"file:   {path}",
                f"format: {img.format}",
                f"mode:   {img.mode}",
                f"size:   {img.size[0]} x {img.size[1]} px",
                f"bytes:  {os.path.getsize(path)}",
            ]
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "image_info", exc)
    return "\n".join(lines)


def image_edit(reg, name: str, args: dict) -> str:
    """Resize, crop, rotate, flip, convert, or desaturate an image.

    Operations apply in a sensible order: crop -> resize -> rotate -> flip ->
    grayscale -> convert. Nothing is written until all of them succeed, and the
    source is left untouched unless you pass the same path as `out`.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    if bad := _guard(rt, conf):
        return bad
    src = str(args.get("path", "")).strip()
    if not src:
        return "ERROR: path required"
    try:
        img = _open(src, conf["max_image_mb"])
        applied: list[str] = []

        if args.get("crop"):
            img = img.crop(tuple(int(float(x)) for x in str(args["crop"]).split(",")))
            applied.append(f"crop={args['crop']}")

        width = args.get("width")
        height = args.get("height")
        scale = args.get("scale")
        if scale:
            s = float(scale)
            img = img.resize((max(1, int(img.size[0] * s)), max(1, int(img.size[1] * s))))
            applied.append(f"scale={s}")
        elif width or height:
            w = int(width) if width else 0
            h = int(height) if height else 0
            if w and h:
                new = (w, h)
            elif w:
                new = (w, max(1, int(img.size[1] * w / img.size[0])))
            else:
                new = (max(1, int(img.size[0] * h / img.size[1])), h)
            img = img.resize(new)
            applied.append(f"resize={new[0]}x{new[1]}")

        if args.get("rotate"):
            img = img.rotate(float(args["rotate"]), expand=True)
            applied.append(f"rotate={args['rotate']}")

        flip = str(args.get("flip") or "").lower()
        if flip:
            from PIL import Image as _I  # noqa: PLC0415

            if flip.startswith("h"):
                img = img.transpose(_I.FLIP_LEFT_RIGHT)
            elif flip.startswith("v"):
                img = img.transpose(_I.FLIP_TOP_BOTTOM)
            else:
                raise ImageError("flip must be 'horizontal' or 'vertical'")
            applied.append(f"flip={flip}")

        if args.get("grayscale"):
            img = img.convert("L")
            applied.append("grayscale")

        fmt = str(args.get("format") or conf["default_format"]).upper()
        out = _resolve_out(src, args.get("out"), f".edited.{fmt.lower()}")
        size = _save_img(img, out, fmt, conf["jpeg_quality"])
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "image_edit", exc)

    rt.record("image_edit", f"edited {src} -> {out}",
              {"path": src, "out": out, "ops": applied})
    ops = ", ".join(applied) or "no-op"
    return f"ok: saved {out} ({size} bytes) [{ops}]"


def image_compose(reg, name: str, args: dict) -> str:
    """Combine several images into one: a grid, a row, or a column.

    Handy for before/after comparisons or a contact sheet of screenshots.
    Optionally stamp a watermark across the result.
    """
    rt = get_runtime()
    conf = _cfg(rt)
    if bad := _guard(rt, conf):
        return bad
    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]
    if len(paths) < 1:
        return "ERROR: paths required (comma-separated list, or an array)"
    if len(paths) > 25:
        return "ERROR: at most 25 images per composition"

    try:
        from PIL import Image, ImageDraw  # noqa: PLC0415

        imgs = [_open(p, conf["max_image_mb"]) for p in paths]
        layout = str(args.get("layout") or "grid").lower()
        bg = str(args.get("background") or "white")

        # `or`-defaults silently swallow a legitimate 0 (spacing=0 means
        # "butt the tiles together", cols=0 means "choose for me"). Read them
        # with `is not None` so an explicit 0 is respected.
        spacing = int(args.get("spacing")) if args.get("spacing") is not None else 12
        spacing = max(0, spacing)
        cols = int(args.get("cols")) if args.get("cols") is not None else 0

        if layout == "horizontal":
            cols, rows = len(imgs), 1
        elif layout == "vertical":
            cols, rows = 1, len(imgs)
        else:  # grid
            # cols=0 (unset) means auto: up to 3 across.
            cols = cols if cols > 0 else min(len(imgs), 3)
            rows = (len(imgs) + cols - 1) // cols

        # Normalise tiles to the largest cell so the grid lines up.
        cell_w = max(i.size[0] for i in imgs)
        cell_h = max(i.size[1] for i in imgs)
        tiles = [i.resize((cell_w, cell_h)) for i in imgs]

        total_w = cols * cell_w + (cols - 1) * spacing
        total_h = rows * cell_h + (rows - 1) * spacing
        canvas = Image.new("RGB", (max(1, total_w), max(1, total_h)), bg)
        for n, tile in enumerate(tiles):
            r, c = divmod(n, cols)
            canvas.paste(tile, (c * (cell_w + spacing), r * (cell_h + spacing)))

        watermark = str(args.get("watermark") or "")
        if watermark:
            draw = ImageDraw.Draw(canvas)
            draw.text((10, 10), watermark, fill=(128, 128, 128))

        fmt = str(args.get("format") or conf["default_format"]).upper()
        out = os.path.expanduser(str(args.get("out") or "")) or _resolve_out(
            paths[0], None, f".composed.{fmt.lower()}")
        size = _save_img(canvas, out, fmt, conf["jpeg_quality"])
        for i in imgs:
            try:
                i.close()
            except Exception:
                pass
    except Exception as exc:  # noqa: BLE001
        return _err(rt, "image_compose", exc)

    rt.record("image_compose", f"composed {len(paths)} images -> {out}",
              {"out": out, "count": len(paths), "layout": layout})
    return (f"ok: saved {out} ({size} bytes) "
            f"[{len(paths)} images, {layout}, {cols} cols]")


__all__ = ["image_info", "image_edit", "image_compose", "ImageError"]
