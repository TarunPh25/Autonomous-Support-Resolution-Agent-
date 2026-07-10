"""
Communication tools — send replies to customers and escalate tickets.
send_reply: 5% failure rate
escalate: always succeeds (critical path, must never fail)
"""

import random
import asyncio
import uuid
import logging
from datetime import datetime, timezone

from utils.retry import ToolServiceError

logger = logging.getLogger("agent.tools.communication")

# Track sent replies and escalations
_replies_sent: list = []
_escalations: list = []


async def send_reply(ticket_id: str, message: str) -> dict:
    """
    Send a reply message to the customer for a given ticket.
    
    Simulated failures:
    - 5% chance of failure
    
    Returns:
        dict with success, message_id, timestamp
    """
    await asyncio.sleep(random.uniform(0.01, 0.03))

    # Simulate failure (5%)
    if random.random() < 0.05:
        logger.warning(f"send_reply({ticket_id}): DELIVERY FAILURE")
        raise ToolServiceError(f"Failed to deliver reply for ticket {ticket_id}")

    message_id = f"MSG-{uuid.uuid4().hex[:8].upper()}"
    timestamp = datetime.now(timezone.utc).isoformat()

    record = {
        "ticket_id": ticket_id,
        "message_id": message_id,
        "message": message,
        "timestamp": timestamp
    }
    _replies_sent.append(record)

    logger.info(f"send_reply({ticket_id}): Sent message {message_id}")
    return {
        "success": True,
        "message_id": message_id,
        "timestamp": timestamp
    }


async def escalate(ticket_id: str, summary: str, priority: str = "medium") -> dict:
    """
    Escalate a ticket to a human agent / supervisor.
    This is a critical path tool — it ALWAYS succeeds.
    
    Returns:
        dict with success, escalation_id, assigned_team, priority
    """
    await asyncio.sleep(random.uniform(0.01, 0.02))

    escalation_id = f"ESC-{uuid.uuid4().hex[:8].upper()}"

    # Determine team based on priority and content
    team_mapping = {
        "critical": "Senior Support & Legal",
        "high": "Tier 2 Support",
        "medium": "General Support Queue",
        "low": "General Support Queue"
    }
    assigned_team = team_mapping.get(priority.lower(), "General Support Queue")

    # Certain keywords route to specific teams
    summary_lower = summary.lower()
    if any(kw in summary_lower for kw in ["warranty", "defect", "manufacturing"]):
        assigned_team = "Warranty & Returns Team"
    elif any(kw in summary_lower for kw in ["replacement", "exchange"]):
        assigned_team = "Fulfilment Team"
    elif any(kw in summary_lower for kw in ["fraud", "social engineering", "suspicious"]):
        assigned_team = "Trust & Safety"
    elif any(kw in summary_lower for kw in ["legal", "lawyer", "threatening"]):
        assigned_team = "Senior Support & Legal"

    record = {
        "ticket_id": ticket_id,
        "escalation_id": escalation_id,
        "summary": summary,
        "priority": priority,
        "assigned_team": assigned_team,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    _escalations.append(record)

    logger.info(
        f"escalate({ticket_id}): Escalated as {priority} priority → {assigned_team} "
        f"[{escalation_id}]"
    )
    return {
        "success": True,
        "escalation_id": escalation_id,
        "assigned_team": assigned_team,
        "priority": priority
    }
