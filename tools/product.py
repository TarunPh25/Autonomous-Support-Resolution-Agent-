"""
Product tool — retrieves product metadata including return windows and warranty info.
Simulates failures: timeouts (5%).
"""

import json
import os
import random
import asyncio
import copy
import logging

from utils.retry import ToolTimeoutError

logger = logging.getLogger("agent.tools.product")

_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRODUCTS_FILE = os.path.join(_DATA_DIR, "products.json")

_products_cache: dict = {}


def _load_products():
    """Load and index products by product_id."""
    global _products_cache
    if _products_cache:
        return
    try:
        with open(_PRODUCTS_FILE, "r", encoding="utf-8") as f:
            products = json.load(f)
        _products_cache = {p["product_id"]: p for p in products}
        logger.info(f"Loaded {len(_products_cache)} products")
    except Exception as e:
        logger.error(f"Failed to load products: {e}")
        _products_cache = {}


async def get_product(product_id: str) -> dict:
    """
    Retrieve product metadata by product ID.
    
    Simulated failures:
    - 5% chance of timeout
    
    Returns:
        dict with product_id, name, category, price, warranty_months,
        return_window_days, returnable, notes
    """
    _load_products()

    # Simulate network latency
    await asyncio.sleep(random.uniform(0.01, 0.03))

    # Simulate timeout (5%)
    if random.random() < 0.05:
        logger.warning(f"get_product({product_id}): TIMEOUT")
        raise ToolTimeoutError(f"Product service timed out for {product_id}")

    product = _products_cache.get(product_id)
    if not product:
        return {
            "error": f"Product {product_id} not found",
            "product_id": product_id,
            "found": False
        }

    result = copy.deepcopy(product)
    result["found"] = True
    return result
