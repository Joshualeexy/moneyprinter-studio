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


def generate_title_and_thumbnail_concepts(script: str, topic: str, profile: dict, viral_hooks: list = None) -> dict:
    """
    Generates high-CTR title, thumbnail short text, and ComfyUI SDXL visual prompt
    anchored strictly to the topic domain and scene context, aligned with real viral search inquiries.
    """
    hooks_context = ""
    if viral_hooks:
        hooks_list = "\n".join(f"- \"{h}\"" for h in viral_hooks[:4])
        hooks_context = f"\nHigh-Intent Search Queries (What Viewers Are Actively Searching):\n{hooks_list}\nYou are strongly encouraged to align the title with one of these high-search-volume queries.\n"

    prompt = f"""You are a viral YouTube Shorts and TikTok thumbnail copywriter and creative director.
Given this video script about '{topic}':

"{script}"
{hooks_context}
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

    from runners.worker import _get_llm_config
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
        # Fallback 1: Extract real frame from rendered video if present
        video_path = concept.get("video_path")
        if not video_path or not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            parent_dir = os.path.dirname(output_path)
            if os.path.exists(parent_dir):
                vids = [os.path.join(parent_dir, f) for f in os.listdir(parent_dir) if f.endswith(".mp4")]
                if vids:
                    video_path = vids[0]

        if video_path and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
            logger.info(f"[Thumbnail] ComfyUI off, extracting cinematic video frame from {video_path}")
            for ts in ["00:00:03.5", "00:00:02.0", "00:00:05.0", "00:00:01.0"]:
                cmd = ["ffmpeg", "-y", "-ss", ts, "-i", video_path, "-vframes", "1", "-q:v", "2", base_image_path]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if os.path.exists(base_image_path) and os.path.getsize(base_image_path) > 0:
                    acquired = True
                    break

        if not acquired or not os.path.exists(base_image_path):
            logger.warning("[Thumbnail] ComfyUI and video unavailable, using dramatic dark canvas fallback")
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

        # Draw Bold High-Impact Hook Text with Dynamic Multi-Line Auto-Wrapping & Auto-Scaling
        raw_text = concept.get("thumbnail_text", "THEY HID THIS").strip().upper()
        draw = ImageDraw.Draw(composite)

        max_canvas_w = 880  # Safe inner margin on 1080px canvas
        font_size = 110
        words = raw_text.split()
        if not words:
            words = ["THEY", "HID", "THIS"]

        # Helper to compute wrapped lines
        def wrap_lines(words_list, f_obj):
            lines = []
            curr = []
            for w in words_list:
                cand = " ".join(curr + [w])
                bb = draw.textbbox((0, 0), cand, font=f_obj, stroke_width=8)
                if (bb[2] - bb[0]) <= max_canvas_w:
                    curr.append(w)
                else:
                    if curr:
                        lines.append(" ".join(curr))
                        curr = [w]
                    else:
                        lines.append(w)
                        curr = []
            if curr:
                lines.append(" ".join(curr))
            return lines

        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception:
            font = ImageFont.load_default()

        lines = wrap_lines(words, font)

        # Auto-scale font down until lines fit comfortably within max 3 lines and max_canvas_w
        while (len(lines) > 3 or any((draw.textbbox((0, 0), l, font=font, stroke_width=8)[2] - draw.textbbox((0, 0), l, font=font, stroke_width=8)[0]) > max_canvas_w for l in lines)) and font_size > 52:
            font_size -= 6
            try:
                font = ImageFont.truetype(font_path, font_size)
            except Exception:
                font = ImageFont.load_default()
            lines = wrap_lines(words, font)

        # Compute line heights and total block height
        line_metrics = []
        total_text_h = 0
        line_gap = int(font_size * 0.18)

        for l in lines:
            bb = draw.textbbox((0, 0), l, font=font, stroke_width=8)
            lw = bb[2] - bb[0]
            lh = bb[3] - bb[1]
            line_metrics.append((l, lw, lh, bb))
            total_text_h += lh

        total_text_h += line_gap * max(0, len(lines) - 1)

        # Center vertically around 38% height
        start_y = int(1920 * 0.38 - total_text_h // 2)
        curr_y = start_y

        for line_text, lw, lh, bb in line_metrics:
            lx = (1080 - lw) // 2
            pad_x = int(font_size * 0.35)
            pad_y = int(font_size * 0.20)
            pill_box = [lx - pad_x, curr_y - pad_y, lx + lw + pad_x, curr_y + lh + pad_y]

            # Rounded dark pill with gold stroke
            draw.rounded_rectangle(pill_box, radius=16, fill=(0, 0, 0, 235))
            draw.rounded_rectangle(pill_box, radius=16, outline=(255, 215, 0, 255), width=4)

            # High-impact yellow text with black stroke
            draw.text(
                (lx - bb[0], curr_y - bb[1]),
                line_text,
                font=font,
                fill=(255, 230, 0, 255),
                stroke_width=max(4, int(font_size * 0.06)),
                stroke_fill=(0, 0, 0, 255)
            )
            curr_y += lh + line_gap

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
