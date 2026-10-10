"""odt_tools.py - poster design JSON <-> editable ODT.

design_to_odt : JSON (+ pictures) -> ODT. Every element is a real, movable object:
                text boxes (edit words / font / size / bold / color), shapes, pictures.
odt_to_design : edited ODT -> JSON (+ pictures), ready to be drawn as the final image.

No extra libraries needed: an ODT is a zip with XML files.
"""
import io
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

import time
from typing import List
from urllib.parse import quote

import requests
from PIL import Image, ImageColor

# =================================================================== DESIGN HELPERS
# Canvas: 576 x 864 points (an 8 x 12 inch portrait page, 2:3). Origin = top-left.
# Positions, sizes and font sizes are all in points, so they mean the same thing in the
# ODT file and in the final image.
CANVAS_W, CANVAS_H = 576.0, 864.0

# family -> (regular, bold, italic, bold-italic) font files (Windows names)
FONTS: Dict[str, tuple] = {
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Arial Black": ("ariblk.ttf", "ariblk.ttf", "ariblk.ttf", "ariblk.ttf"),
    "Impact": ("impact.ttf", "impact.ttf", "impact.ttf", "impact.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
}
DEJAVU = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")

TYPES = ("text", "shape", "image")


def to_hex(value: Any, default: str = "#000000") -> str:
    try:
        r, g, b = ImageColor.getrgb(str(value).strip())[:3]
        return "#%02x%02x%02x" % (r, g, b)
    except (ValueError, TypeError, AttributeError):
        return default


def norm_align(value: Any, default: str = "left") -> str:
    v = str(value or default).lower()
    if v in ("left", "start", "justify"):
        return "left"
    if v in ("right", "end"):
        return "right"
    if v == "center":
        return "center"
    return default


def match_font(name: Any, default: str = "Arial") -> str:
    n = str(name or "").strip().strip("'\"").lower()
    for fam in FONTS:
        if n == fam.lower():
            return fam
    for fam in sorted(FONTS, key=len, reverse=True):   # "Segoe UI Semibold" -> "Segoe UI"
        if n.startswith(fam.lower()):
            return fam
    return default


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fill(value: Any) -> str:
    v = str(value or "").strip().lower()
    if v in ("", "none", "transparent"):
        return ""
    return to_hex(v, "")


def sanitize_element(el: Dict[str, Any], idx: int) -> Dict[str, Any]:
    typ = str(el.get("type", "text")).lower()
    if typ not in TYPES:
        typ = "text"

    w = max(4.0, min(_num(el.get("w"), 100), CANVAS_W))
    h = max(4.0, min(_num(el.get("h"), 40), CANVAS_H))
    x = max(0.0, min(_num(el.get("x"), 0), CANVAS_W - w))
    y = max(0.0, min(_num(el.get("y"), 0), CANVAS_H - h))
    opacity = int(_num(el.get("opacity"), 100))
    if opacity <= 0 or opacity > 100:
        opacity = 100

    return {
        "name": str(el.get("name") or f"E{idx}"),
        "type": typ,
        "x": round(x, 1), "y": round(y, 1), "w": round(w, 1), "h": round(h, 1),
        "text": str(el.get("text") or "") if typ == "text" else "",
        "font_family": match_font(el.get("font_family")),
        "font_size": round(max(6.0, min(_num(el.get("font_size"), 18), 200.0)), 1),
        "bold": bool(el.get("bold", False)),
        "italic": bool(el.get("italic", False)),
        "color": to_hex(el.get("color"), "#000000"),
        "align": norm_align(el.get("align")),
        "fill_color": _fill(el.get("fill_color")),
        "opacity": opacity,
        "corner_radius": round(max(0.0, _num(el.get("corner_radius"), 0)), 1),
        "shape": "ellipse" if str(el.get("shape", "")).lower() == "ellipse" else "rect",
        "image_prompt": str(el.get("image_prompt") or "") if typ == "image" else "",
        "image_file": str(el.get("image_file") or ""),
    }


def sanitize_design(raw: Dict[str, Any]) -> Dict[str, Any]:
    elements: List[Dict[str, Any]] = []
    seen = set()
    for i, el in enumerate(raw.get("elements", []) or []):
        clean = sanitize_element(el, i)
        base, n = clean["name"], 1
        while clean["name"] in seen:          # names must be unique (they link ODT <-> JSON)
            n += 1
            clean["name"] = f"{base}_{n}"
        seen.add(clean["name"])
        elements.append(clean)
    return {
        "title": str(raw.get("title") or ""),
        "design_notes": str(raw.get("design_notes") or ""),
        "background_color": to_hex(raw.get("background_color"), "#ffffff"),
        "elements": elements,
    }


# =================================================================== PICTURES (free, no key)
SUFFIX = ", high quality, no text, no letters, no words, no watermark"


def _to_png(data: bytes) -> bytes:
    buf = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def fetch_image_png(prompt: str, w: float, h: float, seed: int = 1, retries: int = 3):
    """Return PNG bytes, or None if the free service is unavailable."""
    longest = 1024
    if w >= h:
        pw, ph = longest, max(256, int(longest * h / w))
    else:
        pw, ph = max(256, int(longest * w / h)), longest
    url = (f"https://image.pollinations.ai/prompt/{quote(prompt + SUFFIX)}"
           f"?width={pw}&height={ph}&nologo=true&seed={seed}")
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            return _to_png(r.content)
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    return None


def placeholder_png(w: float, h: float, color: str = "#2563eb") -> bytes:
    """Soft vertical gradient used when the artwork service is down."""
    pw, ph = 400, max(80, int(400 * h / max(w, 1)))
    r, g, b = [int(to_hex(color, "#2563eb")[i:i + 2], 16) for i in (1, 3, 5)]
    img = Image.new("RGB", (pw, ph))
    px = img.load()
    for y in range(ph):
        t = y / max(1, ph - 1)
        row = (int(r + (255 - r) * 0.6 * t), int(g + (255 - g) * 0.6 * t), int(b + (255 - b) * 0.6 * t))
        for x in range(pw):
            px[x, y] = row
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build_assets(design: dict, retries: int = 3) -> Tuple[Dict[str, bytes], List[str]]:
    """Fetch one picture per image element. Returns ({file: png_bytes}, [names that failed])."""
    assets: Dict[str, bytes] = {}
    failed: List[str] = []
    for i, el in enumerate(design["elements"]):
        if el["type"] != "image":
            continue
        key = f"Pictures/img_{i}.png"
        data = fetch_image_png(el["image_prompt"] or design.get("title", "poster"),
                               el["w"], el["h"], seed=i + 1, retries=retries)
        if data is None:
            failed.append(el["name"])
            data = placeholder_png(el["w"], el["h"], design.get("background_color", "#2563eb"))
        assets[key] = data
        el["image_file"] = key
    return assets, failed

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
}
MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
        "webp": "image/webp", "bmp": "image/bmp"}
PAD = 3.0  # text box inner padding, points


def _q(prefix: str, name: str) -> str:
    return "{%s}%s" % (NS[prefix], name)


def _pt(value: Optional[str]) -> Optional[float]:
    """'28pt' / '1cm' / '0.5in' -> points. Percentages are ignored."""
    if not value:
        return None
    v = value.strip().lower()
    for unit, mult in (("pt", 1.0), ("cm", 28.3465), ("mm", 2.83465), ("in", 72.0), ("px", 0.75)):
        if v.endswith(unit):
            try:
                return float(v[: -len(unit)]) * mult
            except ValueError:
                return None
    return None


# =================================================================== JSON -> ODT
_POS = ('style:wrap="run-through" style:run-through="foreground" '
        'style:vertical-pos="from-top" style:vertical-rel="page" '
        'style:horizontal-pos="from-left" style:horizontal-rel="page"')


def _p(v: float) -> str:
    return f"{float(v):.2f}pt"


NB = "\u00a0"


def _suggestion_page(design: Dict[str, Any], img_keys: Dict[str, str]):
    """Page 2 of the ODT: the AI's suggestions (notes, colours, text format, image ideas).
    Plain paragraphs/table only, so it is ignored when the ODT is read back."""
    els = design["elements"]
    texts = [e for e in els if e["type"] == "text"]
    shapes = [e for e in els if e["type"] == "shape"]
    images = [e for e in els if e["type"] == "image"]

    styles = [
        '<style:style style:name="S_Title" style:family="paragraph" style:master-page-name="Suggestions">'
        '<style:paragraph-properties fo:margin-bottom="4pt"/>'
        '<style:text-properties fo:font-size="22pt" fo:font-weight="bold" fo:color="#0b2a6f" fo:font-family="Arial"/></style:style>',
        '<style:style style:name="S_Note" style:family="paragraph">'
        '<style:paragraph-properties fo:margin-bottom="6pt"/>'
        '<style:text-properties fo:font-size="9pt" fo:font-style="italic" fo:color="#666666" fo:font-family="Arial"/></style:style>',
        '<style:style style:name="S_Head" style:family="paragraph">'
        '<style:paragraph-properties fo:margin-top="12pt" fo:margin-bottom="3pt" fo:keep-with-next="always"/>'
        '<style:text-properties fo:font-size="14pt" fo:font-weight="bold" fo:color="#1d4ed8" fo:font-family="Arial"/></style:style>',
        '<style:style style:name="S_Body" style:family="paragraph">'
        '<style:paragraph-properties fo:margin-bottom="3pt"/>'
        '<style:text-properties fo:font-size="11pt" fo:color="#222222" fo:font-family="Arial"/></style:style>',
        '<style:style style:name="S_CellText" style:family="paragraph">'
        '<style:text-properties fo:font-size="9pt" fo:color="#222222" fo:font-family="Arial"/></style:style>',
        '<style:style style:name="S_CellHeadText" style:family="paragraph">'
        '<style:text-properties fo:font-size="9pt" fo:font-weight="bold" fo:color="#0b2a6f" fo:font-family="Arial"/></style:style>',
        '<style:style style:name="S_Cell" style:family="table-cell">'
        '<style:table-cell-properties fo:padding="3pt" fo:border="0.5pt solid #bbbbbb"/></style:style>',
        '<style:style style:name="S_CellHead" style:family="table-cell">'
        '<style:table-cell-properties fo:padding="3pt" fo:border="0.5pt solid #bbbbbb" fo:background-color="#e8eefc"/></style:style>',
        '<style:style style:name="S_Table" style:family="table">'
        '<style:table-properties style:width="504pt" table:align="left"/></style:style>',
        '<style:style style:name="S_Thumb" style:family="graphic">'
        '<style:graphic-properties draw:stroke="none" draw:fill="none" style:vertical-pos="top" '
        'style:vertical-rel="baseline" fo:padding="0pt" fo:border="none"/></style:style>',
    ]
    widths = (150, 85, 40, 70, 95, 64)
    for i, w in enumerate(widths):
        styles.append(f'<style:style style:name="S_C{i}" style:family="table-column">'
                      f'<style:table-column-properties style:column-width="{w}pt"/></style:style>')

    sw: Dict[str, str] = {}

    def swatch(color: str) -> str:
        if color not in sw:
            sw[color] = f"SW{len(sw)}"
            styles.append(f'<style:style style:name="{sw[color]}" style:family="text">'
                          f'<style:text-properties fo:background-color="{color}"/></style:style>')
        return f'<text:span text:style-name="{sw[color]}">{NB * 6}</text:span> {color}'

    def para(style: str, inner: str) -> str:
        return f'<text:p text:style-name="{style}">{inner}</text:p>'

    out = [para("S_Title", "AI design suggestions"),
           para("S_Note", "Reference page. Edit the poster on page 1 - everything on this page "
                          "is ignored when you upload the file back into the app.")]

    if design.get("design_notes"):
        out += [para("S_Head", "Design notes"), para("S_Body", escape(design["design_notes"]))]

    bg_line = swatch(to_hex(design.get("background_color"), "#ffffff"))
    bg_pic = next((e for e in images if e["w"] >= 500 and e["h"] >= 800 and e["image_prompt"]), None)
    if bg_pic:
        bg_line += escape(f"   + background picture idea: {bg_pic['image_prompt']}")
    out += [para("S_Head", "Background"), para("S_Body", bg_line)]

    counts: Dict[str, int] = {}
    for e in texts:
        counts[e["color"]] = counts.get(e["color"], 0) + 1
    if counts:
        lines = "<text:line-break/>".join(
            f"{swatch(c)} ({n} text box{'es' if n != 1 else ''})"
            for c, n in sorted(counts.items(), key=lambda kv: -kv[1]))
        out += [para("S_Head", "Text colours"), para("S_Body", lines)]

    shape_colors = []
    for e in shapes:
        if e["fill_color"] and e["fill_color"] not in shape_colors:
            shape_colors.append(e["fill_color"])
    if shape_colors:
        out += [para("S_Head", "Banner / shape colours"),
                para("S_Body", "<text:line-break/>".join(swatch(c) for c in shape_colors))]

    if texts:
        def cell(inner: str, head: bool = False) -> str:
            return (f'<table:table-cell table:style-name="{"S_CellHead" if head else "S_Cell"}" '
                    f'office:value-type="string">'
                    f'{para("S_CellHeadText" if head else "S_CellText", inner)}</table:table-cell>')

        rows = ['<table:table-row>' + "".join(cell(h, True) for h in (
            "Text", "Font", "Size (pt)", "Style", "Colour", "Align")) + '</table:table-row>']
        for e in texts:
            style = " + ".join(x for x, on in (("Bold", e["bold"]), ("Italic", e["italic"])) if on) or "Regular"
            prev = e["text"].replace("\n", " / ")
            prev = prev[:38] + ("..." if len(prev) > 38 else "")
            rows.append('<table:table-row>' + "".join(cell(c) for c in (
                escape(prev), escape(e["font_family"]), f'{e["font_size"]:g}', style,
                swatch(e["color"]), e["align"])) + '</table:table-row>')
        cols = "".join(f'<table:table-column table:style-name="S_C{i}"/>' for i in range(len(widths)))
        out += [para("S_Head", "Text format (font, size, style, colour, alignment)"),
                f'<table:table table:name="TextFormat" table:style-name="S_Table">{cols}{"".join(rows)}</table:table>']

    if images:
        out.append(para("S_Head", "Suitable images"))
        for k, e in enumerate(images, 1):
            w_pt = 90.0
            h_pt = min(150.0, w_pt * e["h"] / max(e["w"], 1))
            key = img_keys.get(e["name"], "")
            if key:
                out.append(para("S_Body",
                    f'<draw:frame draw:style-name="S_Thumb" draw:name="SUG_img{k}" text:anchor-type="as-char" '
                    f'svg:width="{w_pt:.1f}pt" svg:height="{h_pt:.1f}pt"><draw:image xlink:href={quoteattr(key)} '
                    f'xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame>'))
            out.append(para("S_Body", escape(f"Image {k}: {e['image_prompt'] or '(no description)'}")))

    return styles, "".join(out)


def design_to_odt(design: Dict[str, Any], assets: Dict[str, bytes]) -> bytes:
    bg = to_hex(design.get("background_color"), "#ffffff")
    styles, frames = [], []
    used: Dict[str, bytes] = {}
    img_keys: Dict[str, str] = {}

    for i, el in enumerate(design["elements"]):
        name = quoteattr(el["name"])
        geom = (f'draw:name={name} draw:z-index="{i}" text:anchor-type="page" '
                f'text:anchor-page-number="1" svg:x="{_p(el["x"])}" svg:y="{_p(el["y"])}" '
                f'svg:width="{_p(el["w"])}" svg:height="{_p(el["h"])}"')
        typ = el["type"]

        if typ == "text":
            fill = el["fill_color"] or "transparent"
            draw_fill = (f'draw:fill="solid" draw:fill-color="{el["fill_color"]}"'
                         if el["fill_color"] else 'draw:fill="none"')
            styles.append(
                f'<style:style style:name="G{i}" style:family="graphic">'
                f'<style:graphic-properties {_POS} draw:stroke="none" {draw_fill} '
                f'fo:padding="{PAD}pt" fo:border="none" '
                f'fo:background-color="{fill}"/></style:style>')
            styles.append(
                f'<style:style style:name="P{i}" style:family="paragraph">'
                f'<style:paragraph-properties fo:text-align="{el["align"]}"/>'
                f'<style:text-properties fo:font-size="{el["font_size"]}pt" '
                f'fo:font-weight="{"bold" if el["bold"] else "normal"}" '
                f'fo:font-style="{"italic" if el["italic"] else "normal"}" '
                f'fo:color="{el["color"]}" fo:font-family={quoteattr(chr(39) + el["font_family"] + chr(39))}/>'
                f'</style:style>')
            body = escape(el["text"]).replace("\n", "<text:line-break/>")
            frames.append(
                f'<draw:frame draw:style-name="G{i}" {geom}><draw:text-box>'
                f'<text:p text:style-name="P{i}">{body}</text:p></draw:text-box></draw:frame>')

        elif typ == "image":
            key = el.get("image_file") or f"Pictures/img_{i}.png"
            data = assets.get(key) or placeholder_png(el["w"], el["h"], bg)
            used[key] = data
            img_keys[el["name"]] = key
            styles.append(
                f'<style:style style:name="G{i}" style:family="graphic">'
                f'<style:graphic-properties {_POS} draw:stroke="none" draw:fill="none" '
                f'fo:padding="0pt" fo:border="none"/></style:style>')
            frames.append(
                f'<draw:frame draw:style-name="G{i}" {geom}><draw:image xlink:href={quoteattr(key)} '
                f'xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame>')

        else:  # shape
            fill = (f'draw:fill="solid" draw:fill-color="{el["fill_color"]}" '
                    f'draw:opacity="{el["opacity"]}%"') if el["fill_color"] else 'draw:fill="none"'
            styles.append(
                f'<style:style style:name="G{i}" style:family="graphic">'
                f'<style:graphic-properties {_POS} draw:stroke="none" {fill}/></style:style>')
            if el["shape"] == "ellipse":
                frames.append(f'<draw:ellipse draw:style-name="G{i}" {geom}/>')
            else:
                radius = f' draw:corner-radius="{_p(el["corner_radius"])}"' if el["corner_radius"] else ""
                frames.append(f'<draw:rect draw:style-name="G{i}" {geom}{radius}/>')

    sug_styles, sug_xml = _suggestion_page(design, img_keys)
    ns_decl = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())
    content_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content {ns_decl} office:version="1.2">
 <office:automatic-styles>{''.join(styles)}{''.join(sug_styles)}</office:automatic-styles>
 <office:body><office:text><text:p>{''.join(frames)}</text:p>{sug_xml}</office:text></office:body>
</office:document-content>"""

    styles_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {ns_decl} office:version="1.2">
 <office:automatic-styles>
  <style:page-layout style:name="pm1">
   <style:page-layout-properties fo:page-width="{CANVAS_W}pt" fo:page-height="{CANVAS_H}pt"
    fo:margin-top="0pt" fo:margin-bottom="0pt" fo:margin-left="0pt" fo:margin-right="0pt"
    fo:background-color="{bg}"/>
  </style:page-layout>
  <style:page-layout style:name="pm2">
   <style:page-layout-properties fo:page-width="{CANVAS_W}pt" fo:page-height="{CANVAS_H}pt"
    fo:margin-top="36pt" fo:margin-bottom="36pt" fo:margin-left="36pt" fo:margin-right="36pt"
    fo:background-color="#ffffff"/>
  </style:page-layout>
 </office:automatic-styles>
 <office:master-styles>
  <style:master-page style:name="Standard" style:page-layout-name="pm1"/>
  <style:master-page style:name="Suggestions" style:page-layout-name="pm2"/>
 </office:master-styles>
</office:document-styles>"""

    entries = "".join(
        f' <manifest:file-entry manifest:full-path={quoteattr(k)} '
        f'manifest:media-type="{MIME.get(k.rsplit(".", 1)[-1].lower(), "image/png")}"/>\n'
        for k in used)
    manifest = f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
{entries}</manifest:manifest>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", manifest, zipfile.ZIP_DEFLATED)
        z.writestr("styles.xml", styles_xml, zipfile.ZIP_DEFLATED)
        z.writestr("content.xml", content_xml, zipfile.ZIP_DEFLATED)
        for k, data in used.items():
            z.writestr(k, data, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


# =================================================================== ODT -> JSON
def _read_styles(root: ET.Element, styles: Dict[str, Dict], fontmap: Dict[str, str]) -> None:
    for ff in root.iter(_q("style", "font-face")):
        fam = ff.get(_q("svg", "font-family"))
        if ff.get(_q("style", "name")) and fam:
            fontmap[ff.get(_q("style", "name"))] = fam.strip("'\"")

    for st in root.iter(_q("style", "style")):
        name = st.get(_q("style", "name"))
        if not name:
            continue
        props: Dict[str, Any] = {}
        tp = st.find(_q("style", "text-properties"))
        pp = st.find(_q("style", "paragraph-properties"))
        gp = st.find(_q("style", "graphic-properties"))
        if tp is not None:
            props["size"] = _pt(tp.get(_q("fo", "font-size")))
            w = tp.get(_q("fo", "font-weight"))
            props["bold"] = None if w is None else w in ("bold", "600", "700", "800", "900")
            it = tp.get(_q("fo", "font-style"))
            props["italic"] = None if it is None else it in ("italic", "oblique")
            col = tp.get(_q("fo", "color"))
            props["color"] = col if col and col.startswith("#") else None
            fn = tp.get(_q("style", "font-name"))
            props["font"] = (fontmap.get(fn, fn) if fn else
                             (tp.get(_q("fo", "font-family")) or "").strip("'\"") or None)
        if pp is not None:
            props["align"] = pp.get(_q("fo", "text-align"))
        if gp is not None:
            bgc = gp.get(_q("fo", "background-color"))
            props["bg"] = bgc if bgc and bgc.startswith("#") else (
                "" if bgc == "transparent" else None)
            props["fill"] = gp.get(_q("draw", "fill"))
            props["fill_color"] = gp.get(_q("draw", "fill-color"))
            op = gp.get(_q("draw", "opacity"))
            try:
                props["opacity"] = float(op.rstrip("%")) if op else None
            except ValueError:
                props["opacity"] = None
        styles[name] = {"parent": st.get(_q("style", "parent-style-name")), "props": props}


def _resolve(name: Optional[str], styles: Dict[str, Dict]) -> Dict[str, Any]:
    chain, out = [], {}
    while name and name in styles and name not in chain:
        chain.append(name)
        name = styles[name]["parent"]
    for n in reversed(chain):
        out.update({k: v for k, v in styles[n]["props"].items() if v is not None})
    return out


def _segments(p: ET.Element, styles: Dict[str, Dict], base: Dict[str, Any]):
    segs = []

    def walk(el: ET.Element, props: Dict[str, Any]) -> None:
        if el.text:
            segs.append((el.text, props))
        for ch in el:
            if ch.tag == _q("text", "span"):
                walk(ch, {**props, **_resolve(ch.get(_q("text", "style-name")), styles)})
            elif ch.tag == _q("text", "s"):
                segs.append((" " * int(ch.get(_q("text", "c"), "1")), props))
            elif ch.tag == _q("text", "tab"):
                segs.append((" ", props))
            elif ch.tag == _q("text", "line-break"):
                segs.append(("\n", props))
            else:
                walk(ch, props)
            if ch.tail:
                segs.append((ch.tail, props))

    walk(p, base)
    return segs


def _dominant(weights: Dict[Any, int], default: Any) -> Any:
    return max(weights, key=weights.get) if weights else default


def _read_text_box(tb: ET.Element, styles: Dict[str, Dict], fallback: Dict[str, Any]):
    paragraphs = [e for e in tb.iter() if e.tag in (_q("text", "p"), _q("text", "h"))]
    lines, first_align = [], None
    sizes: Dict[Any, int] = {}
    colors: Dict[Any, int] = {}
    fonts: Dict[Any, int] = {}
    bold_n = italic_n = total = 0
    for p in paragraphs:
        pstyle = _resolve(p.get(_q("text", "style-name")), styles)
        if first_align is None:
            first_align = pstyle.get("align")
        segs = _segments(p, styles, pstyle)
        lines.append("".join(t for t, _ in segs))
        for t, pr in segs:
            n = len(t.replace("\n", "").strip())
            if not n:
                continue
            total += n
            sizes[pr.get("size") or fallback["font_size"]] = sizes.get(pr.get("size") or fallback["font_size"], 0) + n
            c = pr.get("color") or fallback["color"]
            colors[c] = colors.get(c, 0) + n
            f = match_font(pr.get("font"), fallback["font_family"])
            fonts[f] = fonts.get(f, 0) + n
            bold_n += n if pr.get("bold") else 0
            italic_n += n if pr.get("italic") else 0
    text = "\n".join(lines).strip("\n")
    if total == 0:
        return text, {}
    return text, {
        "font_size": _dominant(sizes, fallback["font_size"]),
        "color": _dominant(colors, fallback["color"]),
        "font_family": _dominant(fonts, fallback["font_family"]),
        "bold": bold_n * 2 > total,
        "italic": italic_n * 2 > total,
        "align": norm_align(first_align, fallback["align"]),
    }


def _walk_drawables(node: ET.Element):
    drawable = {_q("draw", t) for t in ("frame", "rect", "ellipse", "circle", "custom-shape")}
    for ch in node:
        if ch.tag in drawable:
            yield ch
        else:
            yield from _walk_drawables(ch)


def odt_to_design(odt_bytes: bytes, base_design: Dict[str, Any],
                  base_assets: Optional[Dict[str, bytes]] = None
                  ) -> Tuple[Dict[str, Any], Dict[str, bytes]]:
    """Read an edited ODT back into (design JSON, pictures)."""
    base_assets = base_assets or {}
    base_by_name = {e["name"]: e for e in base_design.get("elements", [])}

    with zipfile.ZipFile(io.BytesIO(odt_bytes)) as z:
        names = set(z.namelist())
        content = ET.fromstring(z.read("content.xml"))
        styles_root = ET.fromstring(z.read("styles.xml")) if "styles.xml" in names else None
        styles: Dict[str, Dict] = {}
        fontmap: Dict[str, str] = {}
        for root in (styles_root, content):
            if root is not None:
                _read_styles(root, styles, fontmap)

        bg = base_design.get("background_color")
        if styles_root is not None:
            layouts = {}
            for pl in styles_root.iter(_q("style", "page-layout")):
                plp = pl.find(_q("style", "page-layout-properties"))
                if plp is not None:
                    col = plp.get(_q("fo", "background-color"))
                    layouts[pl.get(_q("style", "name"))] = col if col and col.startswith("#") else None
            first_master = next(iter(styles_root.iter(_q("style", "master-page"))), None)
            if first_master is not None:        # page 1 (the poster) uses the first master page
                col = layouts.get(first_master.get(_q("style", "page-layout-name")))
                if col:
                    bg = col

        body = content.find(_q("office", "body"))
        text_root = body.find(_q("office", "text")) if body is not None else None
        if text_root is None:
            raise ValueError("This file does not look like an ODT text document.")

        found, assets, new_count = [], {}, 0
        for order, node in enumerate(_walk_drawables(text_root)):
            name = node.get(_q("draw", "name")) or ""
            if name.startswith("SUG_"):
                continue                                    # thumbnails on the suggestions page
            base = base_by_name.get(name)
            st = _resolve(node.get(_q("draw", "style-name")), styles)
            tag = node.tag

            tb = node.find(_q("draw", "text-box")) if tag == _q("draw", "frame") else None
            img = node.find(_q("draw", "image")) if tag == _q("draw", "frame") else None
            if tag == _q("draw", "frame") and tb is None and img is None:
                continue                                    # charts, formulas ... not supported

            if base is None:
                new_count += 1
                name = f"N{new_count}"
                while name in base_by_name:
                    new_count += 1
                    name = f"N{new_count}"
                base = {"name": name, "type": "image" if img is not None else
                        ("text" if tb is not None else "shape"),
                        "x": 36, "y": 36, "w": 200, "h": 60, "font_size": 18,
                        "color": "#000000", "font_family": "Arial", "align": "left"}

            x = _pt(node.get(_q("svg", "x")))
            y = _pt(node.get(_q("svg", "y")))
            w = _pt(node.get(_q("svg", "width")))
            h = _pt(node.get(_q("svg", "height")))
            if h is None and tb is not None:
                h = _pt(tb.get(_q("fo", "min-height"))) or _pt(node.get(_q("fo", "min-height")))
            el = dict(base)
            el.update({"name": name,
                       "x": x if x is not None else base["x"], "y": y if y is not None else base["y"],
                       "w": w if w is not None else base["w"], "h": h if h is not None else base["h"]})

            if tb is not None:
                el["type"] = "text"
                text, fmt = _read_text_box(tb, styles, {
                    "font_size": base.get("font_size", 18), "color": base.get("color", "#000000"),
                    "font_family": base.get("font_family", "Arial"), "align": base.get("align", "left")})
                el["text"] = text
                el.update(fmt)
                if st.get("fill") == "solid" and st.get("fill_color"):
                    el["fill_color"] = st["fill_color"]
                elif st.get("fill") == "none":
                    el["fill_color"] = ""
                elif "bg" in st:
                    el["fill_color"] = st["bg"]
                else:
                    el["fill_color"] = base.get("fill_color", "")
            elif img is not None:
                el["type"] = "image"
                href = img.get(_q("xlink", "href"), "")
                if href in names:
                    assets[href] = z.read(href)
                    el["image_file"] = href
                elif base.get("image_file") in base_assets:
                    assets[base["image_file"]] = base_assets[base["image_file"]]
            else:
                el["type"] = "shape"
                shape = "ellipse" if tag in (_q("draw", "ellipse"), _q("draw", "circle")) else "rect"
                eg = node.find(_q("draw", "enhanced-geometry"))
                if eg is not None and "ellipse" in (eg.get(_q("draw", "type")) or ""):
                    shape = "ellipse"
                el["shape"] = shape
                if st.get("fill") == "none":
                    el["fill_color"] = ""
                elif st.get("fill_color"):
                    el["fill_color"] = st["fill_color"]
                if st.get("opacity"):
                    el["opacity"] = int(st["opacity"])
                cr = _pt(node.get(_q("draw", "corner-radius")))
                if cr is not None:
                    el["corner_radius"] = cr
                elif eg is not None and "round" in (eg.get(_q("draw", "type")) or "") \
                        and not el.get("corner_radius"):
                    el["corner_radius"] = 12

            z_raw = node.get(_q("draw", "z-index"))
            z_val = int(z_raw) if z_raw and z_raw.lstrip("-").isdigit() else order
            found.append((z_val, order, el))

    if not found:
        raise ValueError("No text boxes, shapes or pictures found in the ODT. "
                         "Use boxes placed on the page (Insert > Text Box).")

    found.sort(key=lambda t: (t[0], t[1]))
    design = sanitize_design({**base_design, "background_color": bg,
                              "elements": [e for _, _, e in found]})
    return design, assets