"""
Ticket model — represents a customer support ticket ingested by the agent.
"""

from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional


class TicketCategory(str, Enum):
    """Ticket categories inferred from content analysis."""
    REFUND = "refund"
    RETURN = "return"
    ORDER_STATUS = "order_status"
    CANCELLATION = "cancellation"
    PRODUCT_INQUIRY = "product_inquiry"
    DAMAGE_CLAIM = "damage_claim"
    WARRANTY = "warranty"
    WRONG_ITEM = "wrong_item"
    GENERAL_FAQ = "general_faq"
    COMPLAINT = "complaint"
    UNKNOWN = "unknown"


class Ticket(BaseModel):
    """A customer support ticket to be processed by the agent."""
    ticket_id: str
    customer_email: str
    subject: str
    body: str
    source: str = "email"
    created_at: str = ""
    tier: int = 1  # difficulty tier from dataset (not customer tier)
    expected_action: Optional[str] = None  # ground truth for evaluation

    # Fields populated during analysis
    category: Optional[TicketCategory] = None
    extracted_order_id: Optional[str] = None
    extracted_product_id: Optional[str] = None

    class Config:
        use_enum_values = True
