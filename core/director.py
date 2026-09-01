"""
==============================================================================
AI Executive Movie Director & Production Supervisor (core/director.py)
==============================================================================
Acts as the creative director on set:
1. Plans the shot list (segmenting script into 3-second visual beats).
2. Classifies capture methods: STOCK_MOTION (real filmable footage) vs AI_GENERATIVE (unfilmable/subterranean/microscopic).
3. "The Dailies" Supervisor: Evaluates candidate stock videos before download, instantly rejecting misfires (e.g. ice fishing or scuba divers).
4. Directs ComfyUI SDXL with photorealistic scene prompts when stock footage fails.
==============================================================================
"""

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from loguru import logger
from app.services import llm


@dataclass
class DirectorShot:
    index: int
    script_segment: str
    visual_description: str
    capture_method: str  # "STOCK_MOTION" | "AI_GENERATIVE"
    search_query: str
    fallback_query: str
    sdxl_prompt: str
    avoid_concepts: List[str] = field(default_factory=list)


class MovieDirector:
    """
    Supervises visual production, cinematography, and quality assurance.
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.niche_name = profile.get("niche", {}).get("name", "Documentary")
        self.visual_cfg = profile.get("visual", {})
        self.profile_negatives = self.visual_cfg.get("negative_keywords", [])

    def plan_shotlist(self, script: str, topic: str, total_shots: int) -> List[DirectorShot]:
        """
        Deconstructs the narrative into a storyboard of exact shots with capture methods.
        """
        logger.info(f"[Movie Director] Planning cinematic storyboard for '{topic}' ({total_shots} shots)...")

        prompt = f"""You are the Executive Movie Director for a high-retention documentary video.
Niche: {self.niche_name}
Subject: {topic}
Target Shot Count: {total_shots} shots (approximately 2.5 to 3.5 seconds each)

Script:
"{script}"

For EACH of the {total_shots} sequential shots in the script, you must direct:
1. "script_segment": The spoken words happening during this shot.
2. "visual_description": What the audience physically sees on screen.
3. "capture_method":
   - "STOCK_MOTION": Real filmable world footage (drone flyovers, weather, landscapes, city streets, factories, laboratories, supercomputers).
   - "AI_GENERATIVE": UNFILMABLE scenes where cameras cannot physically exist in the real world (pitch-black subglacial lake 4000m deep, extremophile microbes, alien ocean on Europa/Mars, inside nanometer laser vacuum chambers, ancient tombs).
4. "search_query": For STOCK_MOTION, a 2 to 4 word search term anchored to physical reality in '{topic}' (e.g. "semiconductor cleanroom", "silicon wafer robot", "glacier ice shelf"). NEVER search for abstract politics or trade diplomacy (e.g. NEVER search "US-China trade war") because stock sites return random waving flags!
5. "fallback_query": Simple 1-2 word fallback term.
6. "sdxl_prompt": For AI_GENERATIVE or fallback, a photorealistic textless 8k prompt set in '{topic}' (cinematic lighting, national geographic, 35mm photograph, masterwork, textless).
7. "avoid_concepts": List of 2 to 4 forbidden visual concepts that would ruin this shot (e.g. for polar ice: ["ice fishing", "recreation", "scuba diving", "sunny beach"]; for tech: ["food", "casino", "nature", "flags"]).

Return a JSON array of {total_shots} objects. Return ONLY raw valid JSON."""

        from worker import _get_llm_config
        app_cfg = dict(_get_llm_config(self.profile))
        director_model = self.profile.get("llm", {}).get("director_model", "qwen3:8b")
        app_cfg["ollama_model_name"] = director_model
        logger.info(f"[Movie Director] Planning storyboard via fast lightweight '{director_model}'...")

        response = llm._generate_response(prompt, app_config=app_cfg)

        # Immediately offload director model so 100% of GPU VRAM is free for ComfyUI SDXL
        try:
            import requests as _req
            _req.post("http://127.0.0.1:11434/api/generate", json={"model": director_model, "keep_alive": 0}, timeout=5)
            logger.info(f"[Movie Director] Offloaded '{director_model}' from GPU. VRAM is 100% free for ComfyUI.")
        except Exception as _off_err:
            logger.debug(f"[Movie Director] Model offload notice: {_off_err}")

        raw_shots = []
        try:
            cleaned = response.strip()
            if "```" in cleaned:
                parts = cleaned.split("```")
                cleaned = parts[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            parsed = json.loads(cleaned.strip())
            if isinstance(parsed, list):
                raw_shots = parsed
        except Exception as e:
            logger.warning(f"[Movie Director] Fallback storyboard parsing: {e}")

        # If LLM returned fewer or unparseable shots, generate safe defaults
        shots: List[DirectorShot] = []
        topic_clean = topic.strip()
        topic_words = set(topic_clean.lower().split())

        for idx in range(total_shots):
            item = raw_shots[idx] if idx < len(raw_shots) and isinstance(raw_shots[idx], dict) else {}
            
            cap_method = item.get("capture_method", "STOCK_MOTION").upper()
            if cap_method not in {"STOCK_MOTION", "AI_GENERATIVE"}:
                cap_method = "STOCK_MOTION"

            # Anchor search query to topic safely with null protection
            raw_q_val = item.get("search_query")
            if not raw_q_val or not isinstance(raw_q_val, str):
                raw_q_val = f"{topic_clean} cinematic"
            raw_q = re.sub(r'["\']', '', raw_q_val).strip()

            q_words = set(raw_q.lower().split())
            if not (topic_words & q_words):
                search_q = f"{topic_clean} {raw_q}".strip()
            else:
                search_q = raw_q

            raw_fb_val = item.get("fallback_query")
            if not raw_fb_val or not isinstance(raw_fb_val, str):
                raw_fb_val = topic_clean
            raw_fb = re.sub(r'["\']', '', raw_fb_val).strip()

            fb_words = set(raw_fb.lower().split())
            if not (topic_words & fb_words):
                fallback_q = f"{topic_clean} {raw_fb}".strip()
            else:
                fallback_q = raw_fb

            # Avoid concepts
            avoid = item.get("avoid_concepts", [])
            if not isinstance(avoid, list):
                avoid = []
            combined_avoid = list(set([str(a).lower().strip() for a in avoid] + self.profile_negatives))

            # SDXL prompt
            raw_sdxl = str(item.get("sdxl_prompt") or "").strip()
            if not raw_sdxl or topic_clean.lower() not in raw_sdxl.lower():
                sdxl_p = f"dramatic cinematic photorealistic shot of {topic_clean}, {raw_sdxl}, volumetric lighting, 8k, national geographic, textless"
            else:
                sdxl_p = f"{raw_sdxl}, cinematic lighting, 8k, national geographic, textless"

            shot = DirectorShot(
                index=idx + 1,
                script_segment=item.get("script_segment", ""),
                visual_description=item.get("visual_description", search_q),
                capture_method=cap_method,
                search_query=search_q,
                fallback_query=fallback_q,
                sdxl_prompt=sdxl_p,
                avoid_concepts=combined_avoid,
            )
            shots.append(shot)

        generative_count = sum(1 for s in shots if s.capture_method == "AI_GENERATIVE")
        stock_count = len(shots) - generative_count
        logger.info(f"[Movie Director] Storyboard ready: {stock_count} Real Motion Shots | {generative_count} Custom SDXL Shots.")
        return shots

    def judge_stock_clip(self, shot: DirectorShot, video_meta: dict) -> Tuple[bool, str]:
        """
        The Dailies Review: Inspects candidate video title, URL slug, and tags.
        Rejects misfires (ice fishermen, scuba divers, irrelevant sports).
        """
        v_url = (video_meta.get("source_page") or video_meta.get("url") or "").lower()
        v_tags = " ".join(str(t).lower() for t in video_meta.get("tags", []))
        candidate_text = f"{v_url} {v_tags}"

        # 1. Fast Negative Keyword Rejection
        for avoid in shot.avoid_concepts:
            if avoid and avoid.lower() in candidate_text:
                return False, f"Director rejected: matches forbidden concept '{avoid}' in {v_url}"

        # 2. General cross-niche disqualifiers for serious documentaries
        generic_trash = {
            "vlog", "bikini", "resort", "hotel", "cocktail", "party",
            "cooking", "recipe", "workout", "gym", "fitness", "yoga"
        }
        for trash in generic_trash:
            if trash in candidate_text:
                return False, f"Director rejected: matches generic non-documentary tag '{trash}'"

        # 3. Reject generic waving flags and government press rooms unless script explicitly mentions a flag
        flag_terms = {"flag", "waving-flag", "national-flag", "capitol", "white-house", "presidential"}
        if any(f in candidate_text for f in flag_terms) and "flag" not in shot.script_segment.lower():
            return False, f"Director rejected: generic national flag/capitol misfire in '{v_url}'"

        return True, "Director approved"
