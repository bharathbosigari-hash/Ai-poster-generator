"""generator.py - PART 1: the AI analyses your prompt and designs the whole poster as JSON
(fonts, sizes, colors, alignment, background, shapes, and image ideas)."""
import json
import os
import time
from typing import Any, Dict, List, Union

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel, ValidationError

from odt_tools import FONTS, sanitize_design

load_dotenv()


# ---------------------------------------------------------------- schemas
class DesignElement(BaseModel):
    type: str            # "text" | "shape" | "image"
    x: float
    y: float
    w: float
    h: float
    text: str
    font_family: str
    font_size: float
    bold: bool
    italic: bool
    color: str
    align: str
    fill_color: str
    opacity: int
    corner_radius: float
    shape: str           # "rect" | "ellipse"
    image_prompt: str


class PosterDesign(BaseModel):
    title: str
    design_notes: str
    background_color: str
    elements: List[DesignElement]


class AuditItem(BaseModel):
    criterion: str
    passed: bool
    explanation: str


class InstructionAudit(BaseModel):
    items: List[AuditItem]
    overall_score: int
    summary: str


# ---------------------------------------------------------------- helpers
def get_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is not set. Put it in a .env file next to main.py.")
    return genai.Client(api_key=key)


def text_models() -> List[str]:
    models = []
    env = os.getenv("GEMINI_TEXT_MODEL")
    if env:
        models.append(env)
    for m in ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"):
        if m not in models:
            models.append(m)
    return models


def generate_text(contents, config) -> str:
    """Call Gemini text models in order until one works."""
    client = get_client()
    errors = []
    for model in text_models():
        for attempt in range(4):
            try:
                return client.models.generate_content(
                    model=model, contents=contents, config=config).text
            except ServerError as e:          # busy server: wait, retry, then next model
                errors.append(f"{model}: {e}")
                if attempt < 3:
                    time.sleep(3 * (attempt + 1))
            except ClientError as e:          # bad model name / quota: try next model
                errors.append(f"{model}: {e}")
                break
    raise RuntimeError("All text models failed:\n" + "\n".join(errors))


def _call_json(prompt: str, schema, temperature: float) -> str:
    return generate_text(prompt, types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
    ))


# ---------------------------------------------------------------- the design brief
DESIGN_INSTRUCTIONS = f"""You are an expert poster designer. Read the user's request carefully
(topic, audience, tone, every piece of text, dates, names, colors they mention) and design a
complete, professional poster as JSON. YOU decide the font family, font size, bold/italic,
text alignment, text colors, background, shapes and the images that suit the request.

CANVAS: 576 x 864 points, portrait, origin at top-left. x, y, w, h, font_size and
corner_radius are ALL in points.

ELEMENT TYPES (list order = stacking order, first = bottom):
- "shape": filled rectangle or ellipse. shape = "rect" or "ellipse". Use for banners, cards,
  buttons, circles behind icons, footer strips. fill_color = hex, opacity 1-100,
  corner_radius > 0 gives rounded corners.
- "image": an AI-generated picture. image_prompt = one concrete sentence describing a picture
  that suits the topic and palette (e.g. "young smiling software engineer with a laptop,
  blue futuristic city at night, studio lighting"). Never ask for text, logos or letters in images.
- "text": one text box. text may contain \\n for line breaks. One font size, one color and one
  weight per box - to highlight a word in another color/size, use a separate box.

RULES
1. Facts (names, dates, times, prices, phone numbers, URLs, places) must come from the user's
   request. Never invent them. Short generic lines (taglines, button labels such as
   "Enroll Now") that fit the topic are fine. Spell every word correctly.
2. font_family only from: {", ".join(FONTS)}. Pair fonts: a heavy display font (Impact, Arial
   Black, bold Segoe UI) for the headline, a clean font (Segoe UI, Calibri, Arial) for details.
3. Hierarchy: headline 44-64 pt bold; sub-heading 22-32; body 12-18; small print at least 9.
   Text must have strong contrast against what is behind it.
4. Every text box must fit its text: h >= number_of_lines * font_size * 1.25 + 8, and w wide
   enough for the longest line. Keep all elements inside the canvas and text boxes from
   overlapping each other.
5. Layout like a professional marketing poster, using only the sections that suit the request:
   header (brand name as styled text + tagline), big headline split into two boxes with an
   accent color on the key part, a hero image on one side, a banner shape holding the main
   title, a row of 3-4 feature points (circle shape + label), a highlighted info card (date),
   a details row (3 columns), a rounded call-to-action button with text, a footer strip.
6. At most 3 image elements: an optional full-canvas background (x 0, y 0, w 576, h 864;
   soft or dark enough that text stays readable) and one or two content images.
7. For unused fields use: text "" for shape/image; image_prompt "" for text/shape;
   fill_color "" for no fill; font_family "Arial" and font_size 12 for shape/image;
   opacity 100; corner_radius 0; shape "rect"; color "#000000"; align "left".
8. Use 15 to 40 elements. background_color = hex that matches the palette.
9. design_notes = 3 to 5 short sentences explaining your palette, fonts and image choices.
"""


# ---------------------------------------------------------------- pipeline
def submit_user_instructions(user_prompt: str) -> str:
    return user_prompt.strip()


def generate_design_payload(instructions: str) -> str:
    return _call_json(f"{DESIGN_INSTRUCTIONS}\nUSER REQUEST:\n{instructions}", PosterDesign, 0.7)


def validate_design_payload(raw_json: Union[str, dict]) -> Dict[str, Any]:
    try:
        model = (PosterDesign.model_validate_json(raw_json) if isinstance(raw_json, str)
                 else PosterDesign.model_validate(raw_json))
        design = sanitize_design(model.model_dump())
        if not design["elements"]:
            return {"status": "error", "feedback": "The design has no elements."}
        return {"status": "success", "data": design}
    except ValidationError as e:
        return {"status": "error", "feedback": str(e)}


def regenerate_design_payload(current_json: str, feedback: str, instructions: str) -> str:
    return _call_json(
        f"""{DESIGN_INSTRUCTIONS}
USER REQUEST:
{instructions}

The previous JSON failed validation:
{feedback}

Previous JSON:
{current_json}

Return a corrected JSON payload that follows the schema strictly.""",
        PosterDesign, 0.3)