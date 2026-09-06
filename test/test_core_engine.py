"""
Unit Tests for Autonomous Niche Cinema Engine Core Subsystems
"""

import os
import tempfile
import pytest
from pathlib import Path

from core.profile_loader import load_profile
from core.checkpoint import CheckpointManager
from core.director import DirectorShot
from core.motion_graphics import create_radar_pulse_clip


def test_all_14_niche_profiles_load_validly():
    """Validates that all 14 niche profile TOML files load and contain required fields."""
    profiles_dir = Path("profiles")
    profile_files = list(profiles_dir.glob("*.toml"))
    assert len(profile_files) >= 14, f"Expected at least 14 profiles, found {len(profile_files)}"

    for p_file in profile_files:
        profile = load_profile(p_file.stem)
        assert "niche" in profile, f"Missing [niche] section in {p_file}"
        assert "name" in profile["niche"], f"Missing name in {p_file}"
        assert "slug" in profile["niche"], f"Missing slug in {p_file}"
        assert "video_target" in profile, f"Missing [video_target] in {p_file}"
        assert "series" in profile, f"Missing [series] in {p_file}"
        assert "episodes" in profile["series"], f"Missing episodes in {p_file}"
        assert len(profile["series"]["episodes"]) > 0, f"Empty episodes list in {p_file}"


def test_checkpoint_state_transitions():
    """Validates atomic CheckpointManager operations."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = os.path.join(tmp_dir, "pipeline_state_test.json")
        cm = CheckpointManager(state_file)

        # Initial state
        assert cm.exists() is False
        assert cm.load() is None

        # Save stage state
        state_data = {
            "task_id": "test_123",
            "phase": "script_generated",
            "script": "Line 1. Line 2."
        }
        cm.save(state_data)
        assert cm.exists() is True
        loaded = cm.load()
        assert loaded is not None
        assert loaded["phase"] == "script_generated"
        assert loaded["script"] == "Line 1. Line 2."
        assert "_updated_at" in loaded

        # Clear checkpoint
        cm.clear()
        assert cm.exists() is False


def test_director_shot_schema():
    """Validates DirectorShot dataclass defaults and serialization."""
    shot = DirectorShot(
        index=1,
        script_segment="The nuclear ramjet cruised at Mach 3.",
        visual_description="Cinematic tracking shot of supersonic missile over desert",
        capture_method="AI_GENERATIVE",
        search_query="supersonic missile cruise desert",
        fallback_query="military aircraft supersonic",
        sdxl_prompt="cinematic 8k photograph of glowing atomic cruise missile",
        avoid_concepts=["cartoon", "cgi", "blurry"]
    )
    assert shot.index == 1
    assert shot.capture_method == "AI_GENERATIVE"
    assert "ramjet" in shot.script_segment
    assert len(shot.avoid_concepts) == 3


def test_procedural_motion_graphics_render():
    """Validates procedural PIL animation generation without crashes."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_clip = os.path.join(tmp_dir, "test_radar.mp4")
        result = create_radar_pulse_clip(
            output_path=output_clip,
            target_label="TEST RADAR CONTACT",
            duration=1.0,
            fps=15
        )
        assert os.path.exists(result), "Radar procedural animation clip was not created"
        assert os.path.getsize(result) > 0, "Radar clip is empty"
