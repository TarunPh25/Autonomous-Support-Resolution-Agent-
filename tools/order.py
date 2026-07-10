"""
Order tool — retrieves order details from the order database.
Simulates real-world failures: timeouts (10%), partial data (5%).

NOTE: Order data is loaded from customers.json (the file contains order records
despite its name — this is from the original dataset).
"""

import json
import os
import random
import asyncio
import copy
import logging

from utils.retry import ToolTimeoutError, ToolServiceError

logger = logging.getLogger("agent.tools.order")

# Load order data
_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORDERS_FILE = os.path.join(_DATA_DIR, "customers.json")  # Contains order data

_orders_cache: dict = {}


def _load_orders():
    """Load and index orders by order_id."""
    global _orders_cache
    if _orders_cache:
        return
    try:
        with open(_ORDERS_FILE, "r", encoding="utf-8") as f:
            orders = json.load(f)
        _orders_cache = {o["order_id"]: o for o in orders}
        logger.info(f"Loaded {len(_orders_cache)} orders from {_ORDERS_FILE}")
    except Exception as e:
        logger.error(f"Failed to load orders: {e}")
        _orders_cache = {}


async def get_order(order_id: str) -> dict:
    """
    Retrieve order details by order ID.
    
    Simulated failures:
    - 10% chance of timeout
    - 5% chance of partial data (missing fields)
    
    Returns:
        dict with order_id, customer_id, product_id, quantity, amount,
        status, order_date, delivery_date, return_deadline, refund_status, notes
    """
    _load_orders()

    # Simulate network latency
    await asyncio.sleep(random.uniform(0.01, 0.05))

    # Simulate timeout (10%)
    if random.random() < 0.10:
        logger.warning(f"get_order({order_id}): TIMEOUT")
        raise ToolTimeoutError(f"Order service timed out for {order_id}")

    # Check if order exists
    order = _orders_cache.get(order_id)
    if not order:
        return {
            "error": f"Order {order_id} not found",
            "order_id": order_id,
            "found": False
        }

    result = copy.deepcopy(order)
    result["found"] = True

    # Simulate partial data (5%) — remove some non-critical fields
    if random.random() < 0.05:
        logger.warning(f"get_order({order_id}): Partial data returned")
        fields_to_drop = random.choice([["delivery_date"], ["notes"], ["return_deadline"]])
        for field in fields_to_drop:
            result.pop(field, None)
        result["_partial_data"] = True

    return result
