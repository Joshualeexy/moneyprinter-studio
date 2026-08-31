#!/usr/bin/env python3
"""
==============================================================================
ComfyUI SDXL Client (Textless Cinematic Generation)
==============================================================================
Orchestrates textless cinematic generation via local ComfyUI instance with
juggernautXL_ragnarok.safetensors.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import requests
from loguru import logger

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
NEGATIVE_PROMPT = (
    "(text, typography, font, letters, words, numbers, writing, logo, watermark, signature, "
    "label, brand name, sticker, price tag, dollar sign, currency:1.4), lowres, bad anatomy, "
    "bad hands, error, missing fingers, extra digit, fewer digits, cropped, worst quality, "
    "low quality, normal quality, jpeg artifacts, username, blurry, artist name, deformed, "
    "disfigured, ugly, cartoon, anime, 3d render"
)


class ComfyClient:
    def __init__(self, base_url: str = COMFY_URL):
        self.base_url = base_url

    def is_alive(self) -> bool:
        """Check if ComfyUI server is responsive."""
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_running(self) -> bool:
        """Auto-start ComfyUI if offline as a detached daemon process."""
        if self.is_alive():
            return True
        import subprocess
        logger.info("[ComfyUI] Server is offline, launching ComfyUI on RTX 2070...")
        cmd = "cd /home/kodar/comfyui/ComfyUI && ./venv/bin/python main.py --listen 127.0.0.1 --port 8188 --lowvram --dont-print-server"
        subprocess.Popen(cmd, shell=True, executable="/bin/bash", start_new_session=True)
        for _ in range(35):
            time.sleep(1)
            if self.is_alive():
                logger.info("[ComfyUI] Server is ready and responsive.")
                return True
        return False

    def generate_scene_image(
        self,
        prompt_text: str,
        output_path: str,
        width: int = 832,
        height: int = 1216,
        seed: int = None
    ) -> bool:
        """
        Submits prompt to ComfyUI and waits for output image.
        """
        if not self.ensure_running():
            logger.debug("ComfyUI server is not online.")
            return False

        if seed is None:
            seed = int(time.time() * 1000) % (2**32)

        workflow = {
            "3": {
                "inputs": {
                    "seed": seed,
                    "steps": 15,
                    "cfg": 6.5,
                    "sampler_name": "dpmpp_2m",
                    "scheduler": "karras",
                    "denoise": 1,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0]
                },
                "class_type": "KSampler"
            },
            "4": {
                "inputs": {
                    "ckpt_name": "juggernautXL_ragnarok.safetensors"
                },
                "class_type": "CheckpointLoaderSimple"
            },
            "5": {
                "inputs": {
                    "width": width,
                    "height": height,
                    "batch_size": 1
                },
                "class_type": "EmptyLatentImage"
            },
            "6": {
                "inputs": {
                    "text": f"{prompt_text}, cinematic lighting, 35mm photograph, 8k, detailed texture, masterwork, no text",
                    "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "7": {
                "inputs": {
                    "text": NEGATIVE_PROMPT,
                    "clip": ["4", 1]
                },
                "class_type": "CLIPTextEncode"
            },
            "8": {
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2]
                },
                "class_type": "VAEDecode"
            },
            "9": {
                "inputs": {
                    "filename_prefix": f"MPT_{int(time.time())}",
                    "images": ["8", 0]
                },
                "class_type": "SaveImage"
            }
        }

        try:
            logger.info(f"[ComfyUI] Queuing SDXL render: '{prompt_text[:60]}'...")
            p = {"prompt": workflow}
            data = json.dumps(p).encode("utf-8")
            req = urllib.request.Request(f"{self.base_url}/prompt", data=data)
            prompt_res = json.loads(urllib.request.urlopen(req, timeout=10).read())
            prompt_id = prompt_res["prompt_id"]

            # Poll for completion
            for _ in range(90):
                time.sleep(2)
                try:
                    h_res = self.session.get(f"{self.base_url}/history/{prompt_id}", timeout=20)
                    if h_res.status_code == 200:
                        history_res = h_res.json()
                        if prompt_id in history_res:
                            outputs = history_res[prompt_id].get("outputs", {})
                            if "9" in outputs and "images" in outputs["9"]:
                                img_info = outputs["9"]["images"][0]
                                filename = img_info["filename"]
                                subfolder = img_info.get("subfolder", "")
                                view_url = f"{self.base_url}/view?filename={filename}&subfolder={subfolder}&type=output"
                                
                                r_img = requests.get(view_url, timeout=30)
                                if r_img.status_code == 200:
                                    with open(output_path, "wb") as f:
                                        f.write(r_img.content)
                                    logger.info(f"[ComfyUI] Successfully saved render to {output_path}")
                                    return True
                except Exception as _poll_err:
                    pass
            return False
        except Exception as e:
            logger.warning(f"[ComfyUI] Generation failed: {e}")
            return False
