"""
Customer tool — retrieves customer profiles with tier information.
Simulates failures: timeouts (8%), malformed responses (3%).
"""

import json
import os
import random
import asyncio
import copy
import logging

from utils.retry import ToolTimeoutError, ToolServiceError

logger = logging.getLogger("agent.tools.customer")

_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CUSTOMERS_FILE = os.path.join(_DATA_DIR, "data", "customer_profiles.json")

_customers_cache: dict = {}


def _load_customers():
    """Load and index customers by email."""
    global _customers_cache
    if _customers_cache:
        return
    try:
        with open(_CUSTOMERS_FILE, "r", encoding="utf-8") as f:
            customers = json.load(f)
        _customers_cache = {c["email"]: c for c in customers}
        logger.info(f"Loaded {len(_customers_cache)} customer profiles")
    except Exception as e:
        logger.error(f"Failed to load customer profiles: {e}")
        _customers_cache = {}


async def get_customer(email: str) -> dict:
    """
    Retrieve customer profile by email.
    
    Simulated failures:
    - 8% chance of timeout
    - 3% chance of malformed response
    
    Returns:
        dict with customer_id, email, name, tier, account_age_months,
        total_orders, total_spent, open_tickets, notes
    """
    _load_customers()

    # Simulate network latency
    await asyncio.sleep(random.uniform(0.01, 0.04))

    # Simulate timeout (8%)
    if random.random() < 0.08:
        logger.warning(f"get_customer({email}): TIMEOUT")
        raise ToolTimeoutError(f"Customer service timed out for {email}")

    # Check if customer exists
    customer = _customers_cache.get(email)
    if not customer:
        return {
            "error": f"Customer with email {email} not found in system",
            "email": email,
            "found": False
        }

    result = copy.deepcopy(customer)
    result["found"] = True

    # Simulate malformed response (3%)
    if random.random() < 0.03:
        logger.warning(f"get_customer({email}): Malformed response")
        result["tier"] = None  # Corrupt the tier field
        result["_malformed"] = True

    return result
