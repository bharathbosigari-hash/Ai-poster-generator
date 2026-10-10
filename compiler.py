"""compiler.py - PART 2: draws the final poster IMAGE from the design JSON + pictures.

Text is drawn with Pillow, so spelling is always exact. Each element keeps the position,
font, size, bold/italic, color and alignment stored in the JSON.
"""
import io
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps

from odt_tools import CANVAS_H, CANVAS_W, DEJAVU, FONTS, placeholder_png, to_hex

load_dotenv()

OUT_W = 800
S = OUT_W / CANVAS_W            # points -> pixels
OUT_H = int(round(CANVAS_H * S))
PAD = 3.0                       # must match odt_tools.PAD


@lru_cache(maxsize=256)
def _font(family: str, px: int, bold: bool, italic: bool):
    files = FONTS.get(family, FONTS["Arial"])
    idx = (1 if bold else 0) + (2 if italic else 0)
    for name in (files[idx], files[1 if bold else 0], files[0], DEJAVU[idx], DEJAVU[0]):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def _rgb(value: str):
    h = to_hex(value, "#000000")
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _wrap(draw, text: str, font, max_w: float):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and draw.textlength(trial, font=font) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    return lines


def _box(el):
    return (el["x"] * S, el["y"] * S, (el["x"] + el["w"]) * S, (el["y"] + el["h"]) * S)


def _draw_shape(draw, el, fill):
    box = _box(el)
    if el["shape"] == "ellipse":
        draw.ellipse(box, fill=fill)
    elif el["corner_radius"] > 0:
        draw.rounded_rectangle(box, radius=el["corner_radius"] * S, fill=fill)
    else:
        draw.rectangle(box, fill=fill)


def _draw_image(img, el, assets):
    x0, y0, x1, y1 = [int(round(v)) for v in _box(el)]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    data = assets.get(el.get("image_file", ""))
    try:
        pic = Image.open(io.BytesIO(data)).convert("RGB") if data else None
    except Exception:
        pic = None
    if pic is None:
        pic = Image.open(io.BytesIO(placeholder_png(el["w"], el["h"]))).convert("RGB")
    pic = ImageOps.fit(pic, (w, h), Image.LANCZOS)
    mask = None
    if el["corner_radius"] > 0 or el["shape"] == "ellipse":
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=el["corner_radius"] * S, fill=255)
    img.paste(pic, (x0, y0), mask)


def _draw_text(draw, el):
    if el["fill_color"]:
        _draw_shape(draw, {**el, "shape": "rect"}, _rgb(el["fill_color"]) + (255,))
    px = max(6, int(round(el["font_size"] * S)))
    font = _font(el["font_family"], px, el["bold"], el["italic"])
    asc, desc = font.getmetrics()
    lh = asc + desc
    pad = PAD * S
    x0, y0, x1, _ = _box(el)
    lines = _wrap(draw, el["text"], font, (x1 - x0) - 2 * pad)
    y = y0 + pad
    color = _rgb(el["color"]) + (255,)
    for line in lines:
        w = draw.textlength(line, font=font)
        if el["align"] == "center":
            x = x0 + ((x1 - x0) - w) / 2
        elif el["align"] == "right":
            x = x1 - pad - w
        else:
            x = x0 + pad
        draw.text((x, y), line, font=font, fill=color)
        y += lh


def compile_poster_image(design: Dict, assets: Optional[Dict[str, bytes]] = None,
                         output_path: str = "rendered_poster.png") -> str:
    assets = assets or {}
    img = Image.new("RGB", (OUT_W, OUT_H), _rgb(design.get("background_color", "#ffffff")))
    draw = ImageDraw.Draw(img, "RGBA")

    for el in design["elements"]:
        if el["type"] == "image":
            _draw_image(img, el, assets)
            draw = ImageDraw.Draw(img, "RGBA")
        elif el["type"] == "shape":
            if el["fill_color"]:
                alpha = int(255 * el["opacity"] / 100)
                _draw_shape(draw, el, _rgb(el["fill_color"]) + (alpha,))
        else:
            _draw_text(draw, el)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return str(out)