#!/usr/bin/env python3
"""
==============================================================================
Multi-Source Visual Fetcher: Authentic Internet & Archival Imagery
==============================================================================
Pulls high-resolution, story-relevant imagery from:
1. Wikimedia Commons API (Ancient manuscripts, historical maps, relics, museum scans)
2. Web / Search Image Harvester (DuckDuckGo / Editorial Web)
3. Stealth Scraper Service (Port 4000 if active)
"""

import os
import re
import urllib.parse
from pathlib import Path
from typing import List, Optional
import requests
from loguru import logger
from PIL import Image

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class VisualFetcher:
    """Orchestrates image harvesting across multiple authentic web sources."""

    def __init__(self, scraper_port: int = 4000):
        self.scraper_port = scraper_port
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def search_wikimedia(self, query: str, limit: int = 5) -> List[dict]:
        """
        Queries Wikimedia Commons for authentic historical, archaeological, 
        and documentary public domain images.
        """
        logger.info(f"[Wikimedia] Searching authentic archival images for: '{query}'")
        endpoint = "https://commons.wikimedia.org/w/api.php"
        params = {
            "action": "query",
            "generator": "search",
            "gsrnamespace": 6,  # File namespace
            "gsrsearch": query,
            "gsrlimit": limit * 2,
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": 1280,
            "format": "json",
        }
        
        try:
            resp = self.session.get(endpoint, params=params, timeout=12)
            if resp.status_code != 200:
                logger.warning(f"Wikimedia API returned status {resp.status_code}")
                return []
            
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            results = []
            
            for page in pages.values():
                imageinfo = page.get("imageinfo", [{}])[0]
                url = imageinfo.get("thumburl") or imageinfo.get("url")
                mime = imageinfo.get("mime", "")
                
                # Filter out SVGs, PDFs, sound files, or tiny thumbnails
                if not url or "svg" in mime.lower() or "pdf" in mime.lower() or "audio" in mime.lower():
                    continue
                
                results.append({
                    "title": page.get("title", ""),
                    "url": url,
                    "source": "wikimedia",
                    "width": imageinfo.get("thumbwidth", 0),
                    "height": imageinfo.get("thumbheight", 0)
                })
                if len(results) >= limit:
                    break

            return results
        except Exception as e:
            logger.warning(f"Wikimedia search failed for '{query}': {e}")
            return []

    def search_duckduckgo(self, query: str, limit: int = 5) -> List[dict]:
        """
        Uses duckduckgo-search to find web editorial and photographic imagery.
        """
        try:
            from duckduckgo_search import DDGS
            logger.info(f"[DuckDuckGo] Searching web images for: '{query}'")
            results = []
            with DDGS() as ddgs:
                ddg_results = list(ddgs.images(query, max_results=limit * 2))
                for item in ddg_results:
                    img_url = item.get("image")
                    if img_url and not any(ext in img_url.lower() for ext in [".svg", ".gif"]):
                        results.append({
                            "title": item.get("title", ""),
                            "url": img_url,
                            "source": "duckduckgo",
                            "width": item.get("width", 0),
                            "height": item.get("height", 0)
                        })
                    if len(results) >= limit:
                        break
            return results
        except Exception as e:
            logger.debug(f"DuckDuckGo image search skipped ({e})")
            return []

    def check_stealth_scraper(self) -> bool:
        """Check if port 4000 stealth scraper is currently alive."""
        try:
            r = requests.get(f"http://127.0.0.1:{self.scraper_port}/health", timeout=1.5)
            return r.status_code == 200
        except Exception:
            return False

    def download_image(self, url: str, target_path: str, min_dimension: int = 500) -> bool:
        """
        Downloads an image, validates it via Pillow, and verifies minimum quality.
        """
        try:
            r = self.session.get(url, timeout=15, stream=True)
            if r.status_code != 200:
                return False
            
            with open(target_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

            # Validate image format and dimensions
            with Image.open(target_path) as img:
                w, h = img.size
                if w < min_dimension and h < min_dimension:
                    logger.warning(f"Rejecting image {url}: dimensions too small ({w}x{h})")
                    os.remove(target_path)
                    return False
                
                # Convert RGBA/Palette to RGB JPEG if needed
                if img.mode not in ("RGB", "L"):
                    rgb_img = img.convert("RGB")
                    rgb_img.save(target_path, "JPEG", quality=95)

            return True
        except Exception as e:
            logger.debug(f"Failed to download/validate image {url}: {e}")
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except OSError:
                    pass
            return False

    def harvest_visual_for_scene(
        self,
        scene_keywords: str,
        output_image_path: str,
        fallback_query: str = ""
    ) -> bool:
        """
        Attempts to acquire a high-resolution authentic image for a scene beat
        by querying Wikimedia Commons first, then DuckDuckGo/Web.
        """
        queries_to_try = [scene_keywords]
        if fallback_query and fallback_query != scene_keywords:
            queries_to_try.append(fallback_query)

        for query in queries_to_try:
            # 1. Try Wikimedia Commons (best for historical/science/archival topics)
            items = self.search_wikimedia(query, limit=4)
            for item in items:
                if self.download_image(item["url"], output_image_path):
                    logger.info(f"  ✓ Acquired archival image: {item['title'][:60]} ({item['source']})")
                    return True

            # 2. Try DuckDuckGo / Web images
            items = self.search_duckduckgo(query, limit=4)
            for item in items:
                if self.download_image(item["url"], output_image_path):
                    logger.info(f"  ✓ Acquired web image: {item['title'][:60]} ({item['source']})")
                    return True

        return False
