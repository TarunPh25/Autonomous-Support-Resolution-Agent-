"""
Knowledge Base tool — searches ShopWave policies using keyword relevance matching.
Simulates failures: timeouts (7%), empty results despite relevant query (5%).
"""

import json
import os
import random
import asyncio
import logging
from typing import List

from utils.retry import ToolTimeoutError

logger = logging.getLogger("agent.tools.kb")

_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KB_FILE = os.path.join(_DATA_DIR, "data", "knowledge_base.json")

_kb_cache: list = []


def _load_kb():
    """Load knowledge base articles."""
    global _kb_cache
    if _kb_cache:
        return
    try:
        with open(_KB_FILE, "r", encoding="utf-8") as f:
            _kb_cache = json.load(f)
        logger.info(f"Loaded {len(_kb_cache)} KB articles")
    except Exception as e:
        logger.error(f"Failed to load knowledge base: {e}")
        _kb_cache = []


def _compute_relevance(query: str, article: dict) -> float:
    """
    Compute relevance score between a query and a KB article.
    Uses keyword overlap + content matching for simulated semantic search.
    """
    query_lower = query.lower()
    query_words = set(query_lower.split())

    score = 0.0

    # Check keyword matches (highest weight)
    keywords = [kw.lower() for kw in article.get("keywords", [])]
    for kw in keywords:
        if kw in query_lower:
            score += 0.3
        # Partial match
        for qw in query_words:
            if qw in kw or kw in qw:
                score += 0.1

    # Check title match
    title_lower = article.get("title", "").lower()
    for qw in query_words:
        if qw in title_lower:
            score += 0.15

    # Check content match
    content_lower = article.get("content", "").lower()
    for qw in query_words:
        if len(qw) > 3 and qw in content_lower:  # Skip short words
            score += 0.05

    # Category match
    category = article.get("category", "").lower()
    if category in query_lower:
        score += 0.2

    return min(score, 1.0)


async def search_knowledge_base(query: str) -> dict:
    """
    Search the ShopWave knowledge base for relevant policies.
    
    Simulated failures:
    - 7% chance of timeout
    - 5% chance of returning empty results despite relevant query
    
    Returns:
        dict with 'articles' (list of matching policies with relevance scores)
        and 'query'
    """
    _load_kb()

    # Simulate network latency
    await asyncio.sleep(random.uniform(0.01, 0.05))

    # Simulate timeout (7%)
    if random.random() < 0.07:
        logger.warning(f"search_knowledge_base: TIMEOUT for query '{query}'")
        raise ToolTimeoutError(f"KB service timed out for query: {query}")

    # Simulate empty results (5%)
    if random.random() < 0.05:
        logger.warning(f"search_knowledge_base: Empty results for '{query}'")
        return {"articles": [], "query": query, "_simulated_empty": True}

    # Score all articles
    scored = []
    for article in _kb_cache:
        score = _compute_relevance(query, article)
        if score > 0.1:  # Threshold
            result = {
                "policy_id": article.get("policy_id", ""),
                "title": article.get("title", ""),
                "category": article.get("category", ""),
                "content": article.get("content", ""),
                "relevance_score": round(score, 3)
            }
            scored.append(result)

    # Sort by relevance and return top 3
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    top_results = scored[:3]

    return {
        "articles": top_results,
        "query": query,
        "total_matches": len(scored)
    }
