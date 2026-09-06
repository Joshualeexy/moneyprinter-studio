#!/usr/bin/env python3
"""
==============================================================================
Persistent Asset Registry & Intelligent Candidate Ranker (core/asset_registry.py)
==============================================================================
Tracks visual assets across episodes and niches in a durable SQLite store.
Provides scoring with dynamic cooldown penalties to prevent repetitive visuals
across series while allowing intelligent asset reuse when compositionally sound.
==============================================================================
"""

import hashlib
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional
from loguru import logger

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "storage", "asset_registry.db")


class AssetRegistry:
    """
    Durable SQLite-backed asset registry and ranker.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                asset_id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                media_type TEXT NOT NULL,
                topic TEXT,
                niche TEXT,
                episode_id TEXT,
                used_count INTEGER DEFAULT 1,
                first_used_at REAL NOT NULL,
                last_used_at REAL NOT NULL
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_niche ON assets(niche)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_url ON assets(source_url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_provider ON assets(provider)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assets_last_used ON assets(last_used_at)")

    @staticmethod
    def hash_url(url: str) -> str:
        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]

    def score_candidate(
        self,
        url: str,
        niche: str,
        episode_id: str,
        active_episode_keys: Optional[set] = None
    ) -> float:
        """
        Calculates an asset quality/freshness score from 0.0 to 1.0.
        - Returns 0.0 if already selected in the current episode (zero intra-episode duplicates).
        - Returns 1.0 for completely pristine, never-before-used assets.
        - Returns 0.2-0.85 with dynamic cooldown penalties for previously used assets.
        """
        if not url:
            return 0.0

        # Strict Intra-Episode Uniqueness Guarantee
        if active_episode_keys and url in active_episode_keys:
            return 0.0

        asset_id = self.hash_url(url)
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT used_count, niche, episode_id, last_used_at FROM assets WHERE asset_id = ?",
                    (asset_id,)
                ).fetchone()

                if not row:
                    return 1.0  # Completely fresh asset

                # If already recorded in this exact episode ID
                if episode_id and row["episode_id"] == episode_id:
                    return 0.0

                used_count = row["used_count"]
                prev_niche = row["niche"] or ""
                last_used = row["last_used_at"]
                elapsed_hours = (time.time() - last_used) / 3600.0

                # Base score starts with standard prior-use deduction
                base_score = 0.85

                # Niche proximity penalty
                if prev_niche.lower() == niche.lower():
                    # Heavy cooldown within the same niche
                    if elapsed_hours < 24.0:
                        cooldown_penalty = 0.45  # Used very recently in same niche
                    elif elapsed_hours < 72.0:
                        cooldown_penalty = 0.25
                    else:
                        cooldown_penalty = 0.15
                else:
                    # Milder cooldown across different niches
                    cooldown_penalty = 0.10 if elapsed_hours < 24.0 else 0.05

                # Frequency penalty (repeatedly used clips get degraded further)
                freq_penalty = min(0.20, (used_count - 1) * 0.05)

                final_score = max(0.15, base_score - cooldown_penalty - freq_penalty)
                return round(final_score, 3)

        except Exception as e:
            logger.debug(f"[AssetRegistry] Error scoring candidate {url}: {e}")
            return 0.9

    def register_asset(
        self,
        url: str,
        provider: str,
        media_type: str,
        topic: str,
        niche: str,
        episode_id: str
    ) -> None:
        """
        Records or updates asset usage in the persistent registry.
        """
        if not url:
            return

        asset_id = self.hash_url(url)
        now = time.time()
        try:
            with self._get_connection() as conn:
                conn.execute("""
                INSERT INTO assets (
                    asset_id, source_url, provider, media_type, topic, niche,
                    episode_id, used_count, first_used_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    used_count = used_count + 1,
                    topic = excluded.topic,
                    niche = excluded.niche,
                    episode_id = excluded.episode_id,
                    last_used_at = excluded.last_used_at
                """, (asset_id, url, provider, media_type, topic, niche, episode_id, now, now))
        except Exception as e:
            logger.warning(f"[AssetRegistry] Failed to register asset {url}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """
        Returns high-level statistics for production observability.
        """
        try:
            with self._get_connection() as conn:
                total = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                reused = conn.execute("SELECT COUNT(*) FROM assets WHERE used_count > 1").fetchone()[0]
                providers = {}
                for row in conn.execute("SELECT provider, COUNT(*) as c FROM assets GROUP BY provider"):
                    providers[row["provider"]] = row["c"]
                return {
                    "total_assets": total,
                    "reused_assets": reused,
                    "fresh_assets": total - reused,
                    "providers": providers
                }
        except Exception as e:
            logger.debug(f"[AssetRegistry] Error fetching stats: {e}")
            return {"total_assets": 0, "reused_assets": 0, "fresh_assets": 0, "providers": {}}


# Global singleton instance
registry = AssetRegistry()
