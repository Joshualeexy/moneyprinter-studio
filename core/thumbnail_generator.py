"""
==============================================================================
Viral Title & Thumbnail Generator
==============================================================================
Generates high-CTR curiosity titles, thumbnail text hooks, and custom
photorealistic SDXL visuals via ComfyUI with high-impact typography overlays.
"""

import os
import json
import subprocess
from pathlib import Path
from loguru import logger
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.services import llm
from core.comfy_client import ComfyClient


def generate_title_and_thumbnail_concepts(script: str, topic: str, profile: dict) -> dict:
    """
    Generates high-CTR title, thumbnail short text, and ComfyUI SDXL visual prompt
    anchored strictly to the topic domain and scene context.
    """
    prompt = f"""You are a viral YouTube Shorts and TikTok thumbnail copywriter and creative director.
Given this video script about '{topic}':

"{script}"

Generate:
1. "title": A high-CTR viral video title (under 55 characters, curiosity hook, e.g. "What They Found Under Antarctica Terrifies Scientists").
2. "thumbnail_text": 2 to 4 words MAX for the thumbnail overlay in ALL CAPS (e.g. "DO NOT ENTER", "THEY HID THIS", "IMPOSSIBLE FIND", "BURIED IN ICE"). Must evoke extreme curiosity.
3. "thumbnail_prompt": A detailed textless cinematic SDXL image prompt for ComfyUI.

CRITICAL RULES FOR THUMBNAIL PROMPT:
- The image MUST depict '{topic}' and the core discovery/scene described in the script.
- Every visual element MUST be set directly in the environment of '{topic}'.
- If topic is '{topic}', describe the physical environment, lighting, and subjects of '{topic}' (e.g. "dramatic cinematic photorealistic shot of massive industrial drill rig boring into frozen ice sheet in {topic}, subterranean cavern, volumetric blue lighting, 8k, national geographic photography, textless, no words").
- NEVER depict unrelated rooms, hospitals, generic buildings, or modern city streets.

Return ONLY a JSON object with keys "title", "thumbnail_text", "thumbnail_prompt". No explanations, no markdown."""

    from worker import _get_llm_config
    app_cfg = _get_llm_config(profile)
    resp = llm._generate_response(prompt, app_config=app_cfg)
    try:
        cleaned = resp.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            cleaned = parts[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip())
        if isinstance(data, dict) and "title" in data:
            # Enforce topic anchoring in the prompt
            p_text = data.get("thumbnail_prompt", "")
            if topic.lower() not in p_text.lower():
                data["thumbnail_prompt"] = f"dramatic cinematic shot of {topic}, {p_text}"
            return data
    except Exception as e:
        logger.warning(f"Failed to parse title/thumbnail JSON: {e}")

    return {
        "title": f"The Forbidden Mystery of {topic}",
        "thumbnail_text": "THEY HID THIS",
        "thumbnail_prompt": f"dramatic cinematic photorealistic shot of mysterious discovery buried deep in {topic}, volumetric lighting, 8k, national geographic photography, textless"
    }


def render_thumbnail_image(
    concept: dict,
    output_path: str,
    font_path: str = "resource/fonts/Montserrat-Black.ttf",
    comfy_client: ComfyClient = None
) -> str:
    """
    Generates the SDXL base visual via ComfyUI and overlays bold viral typography.
    """
    if comfy_client is None:
        comfy_client = ComfyClient()

    base_image_path = output_path.replace(".jpg", "_raw.jpg")
    
    # 1. Generate SDXL base visual via ComfyUI
    prompt_txt = concept.get("thumbnail_prompt", "")
    logger.info(f"[Thumbnail] Generating ComfyUI visual for: '{prompt_txt[:60]}...'")
    acquired = False
    if comfy_client.ensure_running():
        acquired = comfy_client.generate_scene_image(prompt_txt, base_image_path)

    if not acquired or not os.path.exists(base_image_path):
        logger.warning("[Thumbnail] ComfyUI unavailable, using dramatic dark canvas fallback")
        img = Image.new("RGB", (1080, 1920), (10, 14, 22))
        img.save(base_image_path)

    # 2. Overlay Viral Typography
    with Image.open(base_image_path).convert("RGBA") as base_img:
        base_img = base_img.resize((1080, 1920), Image.Resampling.LANCZOS)
        
        # Subtle top & bottom cinematic darkening for contrast
        gradient = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        d_grad = ImageDraw.Draw(gradient)
        for y in range(600):
            alpha = int(180 * (1.0 - y / 600.0))
            d_grad.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
        for y in range(1300, 1920):
            alpha = int(200 * ((y - 1300) / 620.0))
            d_grad.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))

        composite = Image.alpha_composite(base_img, gradient)

        # Draw Bold High-Impact Hook Text
        text = concept.get("thumbnail_text", "THEY HID THIS").upper()
        draw = ImageDraw.Draw(composite)

        try:
            font = ImageFont.truetype(font_path, 110)
        except Exception:
            font = ImageFont.load_default()

        # Measure text
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=12)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        # Center horizontally, position at 38% down the frame
        tx = (1080 - tw) // 2
        ty = int(1920 * 0.38 - th // 2)

        # Draw Black Background Badge/Pill behind text for maximum pop
        pad_x = 40
        pad_y = 25
        card_box = [tx - pad_x, ty - pad_y, tx + tw + pad_x, ty + th + pad_y]
        draw.rounded_rectangle(card_box, radius=18, fill=(0, 0, 0, 230))
        draw.rounded_rectangle(card_box, radius=18, outline=(255, 215, 0, 255), width=5)

        # Draw Text: Bright Yellow with heavy black shadow/stroke
        draw.text(
            (tx - bbox[0], ty - bbox[1]),
            text,
            font=font,
            fill=(255, 230, 0, 255),
            stroke_width=6,
            stroke_fill=(0, 0, 0, 255)
        )

        # Save final thumbnail
        final_thumb = composite.convert("RGB")
        final_thumb.save(output_path, "JPEG", quality=96)
        logger.info(f"  ✓ High-CTR Thumbnail rendered: {output_path}")

    # Clean up raw temp base
    if os.path.exists(base_image_path) and base_image_path != output_path:
        try:
            os.remove(base_image_path)
        except OSError:
            pass

    return output_path
