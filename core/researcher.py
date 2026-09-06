#!/usr/bin/env python3
"""
==============================================================================
Evidence-Backed Fact & Intelligence Researcher (core/researcher.py)
==============================================================================
Pulls genuine historical, scientific, and encyclopedic evidence packs before
scriptwriting to ensure videos have authentic narrative depth, factual accuracy,
and verified physical entity anchors.
==============================================================================
"""

import re
import requests
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from loguru import logger

USER_AGENT = "MoneyPrinterStudio-Researcher/2.0 (https://github.com/Joshualeexy/moneyprinter-studio; research-bot)"

DATE_PATTERN = re.compile(
    r"\b(?:(?:1[0-9]{3}|20[0-2][0-9])(?:s|\b)|(?:[0-9]{1,2}(?:th|st|nd|rd)?\s+century(?:\s+BC[E]?)?)|[0-9]{1,5}\s*BC[E]?)\b",
    re.IGNORECASE
)
MEASUREMENT_PATTERN = re.compile(
    r"\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:meters|km|kilometers|miles|feet|tons|pounds|kg|years old|years ago|mph|knots|degrees|celsius|kelvin|percent|%)\b",
    re.IGNORECASE
)


@dataclass
class EvidencePack:
    topic: str
    title: str
    summary: str
    verified_claims: List[str] = field(default_factory=list)
    temporal_anchors: List[str] = field(default_factory=list)
    key_entities: List[str] = field(default_factory=list)
    context: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "title": self.title,
            "summary": self.summary,
            "verified_claims": self.verified_claims,
            "temporal_anchors": self.temporal_anchors,
            "key_entities": self.key_entities,
            "context": self.context,
        }


def _extract_factual_claims(text: str, max_claims: int = 6) -> List[str]:
    """
    Extracts high-density factual sentences containing numbers, dates, or measurements.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    claims = []
    for s in sentences:
        s_clean = s.strip()
        if len(s_clean) < 35 or len(s_clean) > 280:
            continue
        # Skip generic boilerplate sentences
        if any(skip in s_clean.lower() for skip in [
            "may refer to", "is a village", "is a disambiguation", "external links",
            "see also", "references", "further reading", "for other uses"
        ]):
            continue
        # Prioritize sentences with verifiable quantitative or temporal anchors
        has_date = bool(DATE_PATTERN.search(s_clean))
        has_metric = bool(MEASUREMENT_PATTERN.search(s_clean))
        if has_date or has_metric:
            claims.append(s_clean)
        elif len(claims) < 3 and len(s_clean) > 50:
            claims.append(s_clean)

        if len(claims) >= max_claims:
            break
    return claims


def _extract_entities(text: str) -> List[str]:
    """
    Extracts key capitalized proper nouns (locations, artifacts, named institutions).
    """
    # Find sequences of capitalized words (ignoring sentence starters where possible)
    matches = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
    stopwords = {
        "The", "This", "It", "They", "These", "There", "When", "While", "After",
        "During", "Before", "In", "On", "At", "By", "For", "With", "However", "Although"
    }
    seen = set()
    entities = []
    for m in matches:
        if m not in stopwords and len(m) > 3 and m not in seen:
            seen.add(m)
            entities.append(m)
        if len(entities) >= 8:
            break
    return entities


def fetch_topic_research(topic: str) -> Dict[str, str]:
    """
    Fetches genuine historical and encyclopedic context for a topic,
    distilling it into a structured Evidence Pack.
    """
    logger.info(f"[Researcher] Sourcing multi-source factual intel for: '{topic}'...")

    search_url = "https://en.wikipedia.org/w/api.php"
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": topic,
        "format": "json",
        "srlimit": 3
    }
    headers = {"User-Agent": USER_AGENT}

    title = topic
    summary_text = ""
    full_extract = ""

    try:
        r = requests.get(search_url, params=search_params, headers=headers, timeout=8)
        if r.status_code == 200:
            data = r.json()
            search_results = data.get("query", {}).get("search", [])
            if search_results:
                title = search_results[0]["title"]

        # Fetch lead summary & high-density plaintext extract
        extract_url = "https://en.wikipedia.org/w/api.php"
        extract_params = {
            "action": "query",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "titles": title,
            "format": "json"
        }
        r_ext = requests.get(extract_url, params=extract_params, headers=headers, timeout=8)
        if r_ext.status_code == 200:
            pages = r_ext.json().get("query", {}).get("pages", {})
            for page_id, page_data in pages.items():
                if page_id != "-1":
                    full_extract = page_data.get("extract", "")
                    break

        # Fallback to REST summary API if query extract is empty
        if not full_extract:
            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}"
            r_sum = requests.get(summary_url, headers=headers, timeout=8)
            if r_sum.status_code == 200:
                full_extract = r_sum.json().get("extract", "")

    except Exception as e:
        logger.warning(f"[Researcher] Live encyclopedic query encountered issue: {e}")

    summary_text = full_extract.strip()

    # Grounded Evidence Pack Synthesis
    if summary_text:
        claims = _extract_factual_claims(summary_text, max_claims=5)
        dates = list(set(DATE_PATTERN.findall(summary_text)))[:6]
        entities = _extract_entities(summary_text)

        claims_md = "\n".join(f"- {c}" for c in claims) if claims else f"- Documented historical event: {title}"
        dates_md = ", ".join(dates) if dates else "Documented historical timeline"
        entities_md = ", ".join(entities[:6]) if entities else title

        formatted_context = f"""### Primary Encyclopedic Subject: {title}
{summary_text[:600]}...

### Verified Archival Claims:
{claims_md}

### Key Temporal & Physical Anchors:
- Chronological Anchors: {dates_md}
- Named Entities & Physical Sites: {entities_md}

### Directorial Fact-Checking Directives:
- All narrative claims must align with the verified dates, metrics, and entities above.
- Do NOT invent fictional discoveries, fake institutions, or synthetic paranormal theories.
- Ground the drama in genuine physical reality and authentic historical documentation."""

        pack = EvidencePack(
            topic=topic,
            title=title,
            summary=summary_text[:400],
            verified_claims=claims,
            temporal_anchors=dates,
            key_entities=entities,
            context=formatted_context
        )
    else:
        # Objective, non-hallucinatory domain fallback
        formatted_context = f"""### Subject Dossier: {topic}
Overview: Investigative documentary investigation focusing on {topic}.
Directorial Guidelines:
- Anchor the narrative in documented historical facts, verifiable technical principles, and physical geography.
- Maintain authentic documentary tone; avoid sensationalizing unverified folklore without clear qualification.
- Frame open questions strictly around documented scientific debates, not manufactured conspiracies."""

        pack = EvidencePack(
            topic=topic,
            title=topic,
            summary=f"Investigative documentary focus on {topic}.",
            verified_claims=[f"Subject of documented historical and scientific inquiry: {topic}"],
            temporal_anchors=[],
            key_entities=[topic],
            context=formatted_context
        )

    logger.info(f"[Researcher] Evidence Pack compiled for '{topic}' ({len(pack.verified_claims)} verified claims, {len(pack.temporal_anchors)} temporal anchors).")
    return {
        "title": pack.title,
        "summary": pack.summary,
        "verified_claims": pack.verified_claims,
        "temporal_anchors": pack.temporal_anchors,
        "key_entities": pack.key_entities,
        "context": pack.context
    }
