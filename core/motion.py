#!/usr/bin/env python3
"""
==============================================================================
Motion Engine: High-Performance Ken Burns via NVENC
==============================================================================
Transforms still images (web archival photos, ComfyUI SDXL renders) into 
cinematic 1080x1920 30fps vertical video clips using NVIDIA hardware acceleration.
"""

import os
import subprocess
from pathlib import Path
from loguru import logger
from app.utils import utils

# Cinematic motion presets for FFmpeg zoompan filter
# Each preset creates smooth camera gliding motion for vertical 9:16 (1080x1920)
MOTION_PRESETS = [
    # 1. Slow, steady push-in towards center (dramatic focus)
    {
        "name": "zoom_in_center",
        "filter": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.0015,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30"
    },
    # 2. Slow pull-out from center revealing the scene
    {
        "name": "zoom_out_center",
        "filter": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='if(lte(zoom,1.0),1.25,max(1.001,zoom-0.0015))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30"
    },
    # 3. Macro Pan Down (great for scrolls, manuscripts, tall artifacts)
    {
        "name": "pan_down",
        "filter": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z=1.15:x='iw/2-(iw/zoom/2)':y='min(ih/zoom/2+on*0.8,ih-ih/zoom)':d={frames}:s=1080x1920:fps=30"
    },
    # 4. Cinematic Pan Right (landscape/wide shot scanning)
    {
        "name": "pan_right",
        "filter": "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z=1.18:x='min(iw/zoom/2+on*1.0,iw-iw/zoom)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30"
    }
]


def image_to_cinematic_clip(
    image_path: str,
    output_clip_path: str,
    duration: float = 3.5,
    preset_index: int = 0,
    fps: int = 30
) -> str:
    """
    Converts a single high-resolution image into a cinematic 1080x1920 vertical video clip
    using FFmpeg NVENC acceleration.
    """
    if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
        raise FileNotFoundError(f"Source image not found: {image_path}")

    frames = int(duration * fps)
    preset = MOTION_PRESETS[preset_index % len(MOTION_PRESETS)]
    vf_string = preset["filter"].format(frames=frames)

    ffmpeg_bin = utils.get_ffmpeg_binary()
    cmd_nvenc = [
        ffmpeg_bin, "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf_string,
        "-c:v", "h264_nvenc",
        "-preset", "p4", "-tune", "hq",
        "-pix_fmt", "yuv420p",
        "-t", f"{duration:.2f}",
        "-r", str(fps),
        output_clip_path
    ]
    cmd_cpu = [
        ffmpeg_bin, "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf_string,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-t", f"{duration:.2f}",
        "-r", str(fps),
        output_clip_path
    ]

    # Attempt NVENC first, immediately fall back to libx264 if unavailable
    success = False
    try:
        res = subprocess.run(cmd_nvenc, capture_output=True, text=True, check=False)
        if res.returncode == 0 and os.path.exists(output_clip_path) and os.path.getsize(output_clip_path) > 0:
            success = True
    except Exception:
        pass

    if not success:
        if os.path.exists(output_clip_path):
            try:
                os.remove(output_clip_path)
            except OSError:
                pass
        subprocess.run(cmd_cpu, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not os.path.exists(output_clip_path) or os.path.getsize(output_clip_path) == 0:
        raise RuntimeError(f"Failed to render cinematic clip for {image_path}")

    return output_clip_path
