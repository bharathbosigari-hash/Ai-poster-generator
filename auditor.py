"""auditor.py - PART 3: audits the generated poster image against your prompt."""
import json
from typing import Any, Dict, List, Tuple

from google.genai import types
from PIL import Image

from generator import InstructionAudit, generate_text

PASS_SCORE = 70


def audit_poster_image(image_path: str, user_prompt: str, blueprint_json: str) -> Dict[str, Any]:
    try:
        with Image.open(image_path) as im:
            im.verify()
    except Exception as e:
        return {"items": [], "overall_score": 0, "summary": f"Image could not be loaded: {e}"}

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    prompt = f"""You are auditing a generated poster image.

Original user request: {user_prompt}

Blueprint used: {blueprint_json}

Check the image for: theme match, requested text present and spelled correctly,
color scheme, layout/readability, and artwork relevance. Give each criterion a
pass/fail with a short explanation, plus an overall score from 0 to 100."""

    text = generate_text(
        [types.Part.from_bytes(data=image_bytes, mime_type="image/png"), prompt],
        types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=InstructionAudit,
            temperature=0.1,
        ),
    )
    return json.loads(text)


def evaluate_pass_criteria(audit: Dict[str, Any]) -> Tuple[bool, List[str]]:
    failed = [i["criterion"] for i in audit.get("items", []) if not i.get("passed")]
    passed = audit.get("overall_score", 0) >= PASS_SCORE and not failed
    return passed, failed