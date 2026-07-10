"""
Refund tools — check eligibility and issue refunds.
Simulates real-world failures:
  - check_refund_eligibility: 15% service failure, 5% timeout
  - issue_refund: 10% payment processor failure

SAFETY: issue_refund enforces that eligibility MUST be checked first.
"""

import json
import os
import random
import asyncio
import uuid
import logging
from datetime import datetime

from utils.retry import ToolTimeoutError, ToolServiceError

logger = logging.getLogger("agent.tools.refund")

# Load order data for eligibility checks
_DATA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORDERS_FILE = os.path.join(_DATA_DIR, "customers.json")

_orders_cache: dict = {}
_eligibility_checked: set = set()  # Track which orders have been checked
_refunds_issued: set = set()  # Track issued refunds


def _load_orders():
    global _orders_cache
    if _orders_cache:
        return
    try:
        with open(_ORDERS_FILE, "r", encoding="utf-8") as f:
            orders = json.load(f)
        _orders_cache = {o["order_id"]: o for o in orders}
    except Exception as e:
        logger.error(f"Failed to load orders for refund: {e}")


async def check_refund_eligibility(order_id: str) -> dict:
    """
    Check if an order is eligible for a refund based on business rules.
    
    Checks: order status, return window, existing refunds, order existence.
    
    Simulated failures:
    - 15% random service failure
    - 5% timeout
    
    Returns:
        dict with eligible, reason, max_refund_amount, policy_reference, order_status
    """
    _load_orders()

    await asyncio.sleep(random.uniform(0.02, 0.06))

    # Simulate service failure (15%)
    if random.random() < 0.15:
        logger.warning(f"check_refund_eligibility({order_id}): SERVICE FAILURE")
        raise ToolServiceError(f"Refund eligibility service unavailable for {order_id}")

    # Simulate timeout (5%)
    if random.random() < 0.05:
        logger.warning(f"check_refund_eligibility({order_id}): TIMEOUT")
        raise ToolTimeoutError(f"Refund eligibility check timed out for {order_id}")

    order = _orders_cache.get(order_id)
    if not order:
        return {
            "eligible": False,
            "reason": f"Order {order_id} not found in system",
            "max_refund_amount": 0,
            "policy_reference": "",
            "order_status": "not_found"
        }

    # Check if already refunded
    if order.get("refund_status") == "refunded":
        _eligibility_checked.add(order_id)
        return {
            "eligible": False,
            "reason": "Refund has already been processed for this order",
            "max_refund_amount": 0,
            "policy_reference": "POL-002 (Refund Processing Policy)",
            "order_status": order["status"]
        }

    # Check order status
    status = order.get("status", "unknown")
    amount = order.get("amount", 0)

    if status == "processing":
        # Can cancel, not refund
        _eligibility_checked.add(order_id)
        return {
            "eligible": True,
            "reason": "Order is still in processing — eligible for cancellation and full refund",
            "max_refund_amount": amount,
            "policy_reference": "POL-005 (Order Cancellation Policy)",
            "order_status": status
        }

    if status == "shipped":
        _eligibility_checked.add(order_id)
        return {
            "eligible": False,
            "reason": "Order has been shipped and is in transit. Cannot refund until delivered. Customer should wait for delivery and initiate a return.",
            "max_refund_amount": 0,
            "policy_reference": "POL-005 (Order Cancellation Policy), POL-001 (Standard Return Policy)",
            "order_status": status
        }

    if status == "delivered":
        # Check return window
        return_deadline = order.get("return_deadline")
        if return_deadline:
            try:
                deadline = datetime.strptime(return_deadline, "%Y-%m-%d")
                # Use ticket creation as "now" since data is from 2024
                # For simulation, we compare with a fixed reference date
                reference_date = datetime(2024, 3, 15)
                if reference_date > deadline:
                    _eligibility_checked.add(order_id)
                    return {
                        "eligible": False,
                        "reason": f"Return window expired on {return_deadline}. Current date is past the return deadline.",
                        "max_refund_amount": 0,
                        "policy_reference": "POL-001 (Standard Return Policy)",
                        "order_status": status
                    }
            except ValueError:
                pass

        # Within return window
        _eligibility_checked.add(order_id)
        return {
            "eligible": True,
            "reason": "Order is delivered and within return window. Eligible for refund.",
            "max_refund_amount": amount,
            "policy_reference": "POL-001 (Standard Return Policy), POL-002 (Refund Processing Policy)",
            "order_status": status
        }

    # Unknown status
    _eligibility_checked.add(order_id)
    return {
        "eligible": False,
        "reason": f"Order has unexpected status: {status}",
        "max_refund_amount": 0,
        "policy_reference": "",
        "order_status": status
    }


async def issue_refund(order_id: str, amount: float) -> dict:
    """
    Issue a refund for an order. THIS IS AN IRREVERSIBLE ACTION.
    
    Pre-condition: check_refund_eligibility MUST have been called first.
    
    Simulated failures:
    - 10% payment processor failure
    
    Returns:
        dict with success, transaction_id, refunded_amount, message
    """
    await asyncio.sleep(random.uniform(0.02, 0.08))

    # SAFETY CHECK: Eligibility must be checked first
    if order_id not in _eligibility_checked:
        logger.error(f"issue_refund({order_id}): BLOCKED — eligibility not checked")
        return {
            "success": False,
            "transaction_id": "",
            "refunded_amount": 0,
            "message": "SAFETY VIOLATION: Cannot issue refund without checking eligibility first. Call check_refund_eligibility before issue_refund."
        }

    # Check for duplicate refund
    if order_id in _refunds_issued:
        return {
            "success": False,
            "transaction_id": "",
            "refunded_amount": 0,
            "message": f"Refund already issued for order {order_id}. Cannot process duplicate refund."
        }

    # Validate amount
    if amount <= 0:
        return {
            "success": False,
            "transaction_id": "",
            "refunded_amount": 0,
            "message": "Invalid refund amount. Must be positive."
        }

    # Simulate payment processor failure (10%)
    if random.random() < 0.10:
        logger.warning(f"issue_refund({order_id}): PAYMENT PROCESSOR FAILURE")
        raise ToolServiceError(f"Payment processor error for order {order_id}. Transaction declined.")

    # Success
    transaction_id = f"TXN-{uuid.uuid4().hex[:8].upper()}"
    _refunds_issued.add(order_id)

    logger.info(f"issue_refund({order_id}): SUCCESS — ${amount:.2f} refunded, TXN: {transaction_id}")
    return {
        "success": True,
        "transaction_id": transaction_id,
        "refunded_amount": amount,
        "message": f"Refund of ${amount:.2f} successfully processed. Transaction ID: {transaction_id}. Customer will receive funds within 5-7 business days."
    }
