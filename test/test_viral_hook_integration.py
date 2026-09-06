import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.researcher import fetch_topic_research, EvidencePack
from worker import build_system_script_prompt, _get_llm_config
from core.profile_loader import load_profile

def test_viral_hook_extraction():
    topic = "Voynich manuscript"
    print(f"Testing live topic research for '{topic}'...")
    pack = fetch_topic_research(topic)
    
    assert isinstance(pack, dict), "Result must be a dict instance"
    print(f"✓ EvidencePack dict generated successfully for: '{pack.get('title')}'")
    print(f"✓ Summary length: {len(pack.get('summary', ''))} chars")
    print(f"✓ Verified claims count: {len(pack.get('verified_claims', []))}")
    print(f"✓ Temporal anchors count: {len(pack.get('temporal_anchors', []))}")
    print(f"✓ Viral hooks count: {len(pack.get('viral_hooks', []))}")
    
    viral_hooks = pack.get("viral_hooks", [])
    if viral_hooks:
        print("\nExtracted High-Intent Viral Inquiry Hooks:")
        for idx, h in enumerate(viral_hooks, 1):
            print(f"  [{idx}] {h}")
        assert any("voynich" in h.lower() or "?" in h for h in viral_hooks), "Viral hooks should contain relevant search questions"
        assert "High-Intent Viral Inquiry Angles" in pack["context"], "Context must include the Viral Inquiry section"
    else:
        print("Note: Scraper returned no PAA or fell back to Wikipedia.")

    # Test prompt synthesis
    profile = load_profile("unsolved_mysteries")
    prompt = build_system_script_prompt(profile, topic, research_context=pack["context"])
    
    assert "VIRAL INQUIRY ANGLE" in prompt, "Prompt must contain the VIRAL INQUIRY ANGLE directive"
    if viral_hooks:
        assert "High-Intent Viral Inquiry Angles" in prompt, "Prompt must contain the extracted viral questions"
    print("\n✓ System Scriptwriter prompt correctly incorporates viral inquiry vectors!")
    print("\nALL CHECKS PASSED!")

if __name__ == "__main__":
    test_viral_hook_extraction()
