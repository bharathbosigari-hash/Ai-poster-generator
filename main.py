"""main.py - PART 4: Streamlit UI.

Prompt -> AI designs the poster (JSON) -> pictures + editable ODT -> you edit in LibreOffice
-> upload ODT -> JSON -> final poster image.
"""
import json
import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from auditor import audit_poster_image, evaluate_pass_criteria
from compiler import compile_poster_image
from generator import (
    generate_design_payload,
    regenerate_design_payload,
    submit_user_instructions,
    validate_design_payload,
)
from odt_tools import build_assets, design_to_odt, odt_to_design

# ------------------------------------------------------------ AI suggestions panel
def _swatch(color: str) -> str:
    return (f'<span style="display:inline-block;width:14px;height:14px;border-radius:3px;'
            f'background:{color};border:1px solid #888;margin-right:6px;vertical-align:middle">'
            f'</span><code>{color}</code>')


def summarize_design(d: dict) -> dict:
    """Turn the design JSON into plain suggestions: text format, colours, background, images."""
    els = d["elements"]
    texts = [e for e in els if e["type"] == "text"]
    shapes = [e for e in els if e["type"] == "shape"]
    images = [e for e in els if e["type"] == "image"]

    text_colors = {}
    for e in texts:
        text_colors[e["color"]] = text_colors.get(e["color"], 0) + 1
    shape_colors = []
    for e in shapes:
        if e["fill_color"] and e["fill_color"] not in shape_colors:
            shape_colors.append(e["fill_color"])

    rows = []
    for e in texts:
        style = " + ".join(x for x, on in (("Bold", e["bold"]), ("Italic", e["italic"])) if on) or "Regular"
        preview = e["text"].replace("\n", " / ")
        rows.append({"Text": preview[:38] + ("..." if len(preview) > 38 else ""),
                     "Font": e["font_family"], "Size (pt)": e["font_size"], "Style": style,
                     "Colour": e["color"], "Align": e["align"]})

    return {
        "rows": rows,
        "text_colors": sorted(text_colors.items(), key=lambda kv: -kv[1]),
        "shape_colors": shape_colors,
        "images": images,
        "bg_image": next((e for e in images if e["w"] >= 500 and e["h"] >= 800), None),
    }


def show_suggestions(d: dict, assets: dict) -> None:
    s = summarize_design(d)
    st.subheader("🧠 AI suggestions")
    if d.get("design_notes"):
        st.info(d["design_notes"])

    bg_text = _swatch(d["background_color"])
    if s["bg_image"] is not None and s["bg_image"]["image_prompt"]:
        bg_text += f" &nbsp;+ background picture idea: <i>{s['bg_image']['image_prompt']}</i>"
    st.markdown("**🎨 Background**<br>" + bg_text, unsafe_allow_html=True)

    if s["text_colors"]:
        st.markdown("**🖍 Text colours**<br>" + " &nbsp;&nbsp; ".join(
            f"{_swatch(c)} x{n}" for c, n in s["text_colors"]), unsafe_allow_html=True)
    if s["shape_colors"]:
        st.markdown("**🔷 Banner / shape colours**<br>" + " &nbsp;&nbsp; ".join(
            _swatch(c) for c in s["shape_colors"]), unsafe_allow_html=True)

    st.markdown("**🔤 Text format (font, size, style, alignment)**")
    st.table(s["rows"])

    st.markdown("**🖼 Suitable images**")
    if not s["images"]:
        st.write("No pictures suggested for this poster.")
    for e in s["images"]:
        data = assets.get(e.get("image_file", ""))
        if data:
            st.image(data, caption=e["image_prompt"], width=220)
        else:
            st.write(f"• {e['image_prompt']} _(picture appears after Step 2)_")


st.set_page_config(page_title="AI Poster Generator", page_icon="🎨", layout="centered")
st.title("🎨 AI Poster Generator")

if not os.getenv("GEMINI_API_KEY"):
    st.error("GEMINI_API_KEY not found. Create a .env file next to main.py containing:\n\nGEMINI_API_KEY=your_key_here")
    st.stop()

for key, default in (("design", None), ("assets", {}), ("image_path", None),
                     ("image_bytes", None), ("audit", None)):
    st.session_state.setdefault(key, default)

# ------------------------------------------------------------ PROMPT FIRST
user_prompt = st.text_area(
    "Describe the poster you want:",
    placeholder="e.g., Poster for an AI Engineer course (Full Stack + Agentic AI) by Ojasa Mirai, "
                "new batch from Monday 12 October 2026, Mon-Thu 7:30-8:30 PM, online and offline, "
                "blue and orange modern look, young student with laptop",
    height=140,
)

# ------------------------------------------------------------ STEP 1: AI designs the poster
if st.button("Step 1: AI designs the poster (JSON)", type="primary"):
    if not user_prompt.strip():
        st.warning("Please enter a prompt first.")
    else:
        try:
            instructions = submit_user_instructions(user_prompt)
            with st.spinner("The AI is designing your poster..."):
                raw = generate_design_payload(instructions)
                result = validate_design_payload(raw)
                if result["status"] == "error":
                    raw = regenerate_design_payload(raw, result["feedback"], instructions)
                    result = validate_design_payload(raw)
            if result["status"] == "success":
                st.session_state.update(design=result["data"], assets={}, image_path=None,
                                        image_bytes=None, audit=None)
                st.success("✅ Design ready")
            else:
                st.error(f"Validation failed: {result['feedback']}")
        except Exception as e:
            st.error(f"Design generation failed:\n\n{e}")

design = st.session_state.design
if design:
    st.divider()
    show_suggestions(design, st.session_state.assets)
    with st.expander("📋 Design JSON"):
        st.code(json.dumps(design, indent=2, ensure_ascii=False), language="json")

    # -------------------------------------------------------- STEP 2: pictures + ODT
    st.subheader("Step 2: Pictures + editable ODT")
    if st.button("Fetch pictures and build ODT"):
        try:
            with st.spinner("Creating pictures (about 10-30 seconds each)..."):
                assets, failed = build_assets(design)
            st.session_state.assets = assets
            if failed:
                st.warning("The free picture service did not answer for: " + ", ".join(failed)
                           + ". Gradient placeholders were used. Click the button again to retry.")
            else:
                st.success("✅ Pictures ready")
        except Exception as e:
            st.error(f"Picture creation failed:\n\n{e}")

    if st.session_state.assets or not any(e["type"] == "image" for e in design["elements"]):
        st.download_button("📄 Download editable ODT",
                           design_to_odt(design, st.session_state.assets),
                           "poster_edit.odt", "application/vnd.oasis.opendocument.text")
        st.caption("Open it in LibreOffice. Click any text box to change the words, font, size, "
                   "bold or color. Drag boxes to move or resize them. Click a shape to change its "
                   "color. Add new text with Insert > Text Box. Save as .odt.")

    # -------------------------------------------------------- STEP 3: ODT -> JSON
    st.subheader("Step 3: Upload the edited ODT")
    uploaded = st.file_uploader("Edited .odt file", type=["odt"])
    if uploaded is not None and st.button("Convert ODT to JSON"):
        try:
            new_design, new_assets = odt_to_design(
                uploaded.getvalue(), st.session_state.design, st.session_state.assets)
            st.session_state.update(design=new_design, assets=new_assets, image_path=None,
                                    image_bytes=None, audit=None)
            st.success("✅ JSON updated from your ODT")
            st.rerun()
        except Exception as e:
            st.error(f"Could not read the ODT:\n\n{e}")

    # -------------------------------------------------------- STEP 4: JSON -> image
    st.subheader("Step 4: Generate the poster image")
    if st.button("Generate Poster Image"):
        try:
            with st.spinner("Drawing the poster..."):
                path = compile_poster_image(st.session_state.design, st.session_state.assets)
                with open(path, "rb") as f:
                    st.session_state.image_bytes = f.read()
                st.session_state.image_path = path
                st.session_state.audit = None
        except Exception as e:
            st.error(f"Image generation failed:\n\n{e}")

# ------------------------------------------------------------ IMAGE + AUDIT
if st.session_state.image_bytes:
    st.divider()
    st.image(st.session_state.image_bytes, use_container_width=True)
    st.download_button("📥 Download Poster (PNG)", st.session_state.image_bytes,
                       "poster.png", "image/png")

    if st.button("Step 5: Audit Poster"):
        try:
            with st.spinner("Auditing..."):
                st.session_state.audit = audit_poster_image(
                    st.session_state.image_path, user_prompt,
                    json.dumps(st.session_state.design, ensure_ascii=False))
        except Exception as e:
            st.error(f"Audit failed:\n\n{e}")

if st.session_state.audit:
    audit = st.session_state.audit
    passed, failed = evaluate_pass_criteria(audit)
    st.metric("Adherence Score", f"{audit.get('overall_score', 0)} / 100")
    (st.success if passed else st.warning)(audit.get("summary", ""))
    for item in audit.get("items", []):
        with st.expander(f"{'✅' if item['passed'] else '❌'} {item['criterion']}"):
            st.write(item["explanation"])
    if not passed:
        st.info("Not happy? Edit the ODT and upload it again, or run Step 1 again for a new design.")