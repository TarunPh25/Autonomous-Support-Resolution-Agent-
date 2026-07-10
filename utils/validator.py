"""
Schema validation for tool inputs and outputs using Pydantic.
Handles missing fields, malformed data, and type coercion gracefully.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, Any
import logging

logger = logging.getLogger("agent.validator")


# ──────────────────────────────────────────────
# ORDER SCHEMAS
# ──────────────────────────────────────────────

class OrderInput(BaseModel):
    order_id: str

class OrderOutput(BaseModel):
    order_id: str
    customer_id: str = ""
    product_id: str = ""
    quantity: int = 1
    amount: float = 0.0
    status: str = "unknown"
    order_date: Optional[str] = None
    delivery_date: Optional[str] = None
    return_deadline: Optional[str] = None
    refund_status: Optional[str] = None
    notes: str = ""


# ──────────────────────────────────────────────
# CUSTOMER SCHEMAS
# ──────────────────────────────────────────────

class CustomerInput(BaseModel):
    email: str

class CustomerOutput(BaseModel):
    customer_id: str = ""
    email: str
    name: str = "Unknown"
    tier: str = "standard"
    account_age_months: int = 0
    total_orders: int = 0
    total_spent: float = 0.0
    open_tickets: int = 0
    notes: str = ""


# ──────────────────────────────────────────────
# PRODUCT SCHEMAS
# ──────────────────────────────────────────────

class ProductInput(BaseModel):
    product_id: str

class ProductOutput(BaseModel):
    product_id: str
    name: str = "Unknown Product"
    category: str = ""
    price: float = 0.0
    warranty_months: int = 0
    return_window_days: int = 30
    returnable: bool = True
    notes: str = ""


# ──────────────────────────────────────────────
# KNOWLEDGE BASE SCHEMAS
# ──────────────────────────────────────────────

class KBSearchInput(BaseModel):
    query: str

class KBArticle(BaseModel):
    policy_id: str = ""
    title: str
    category: str = ""
    content: str
    relevance_score: float = 0.0

class KBSearchOutput(BaseModel):
    articles: list[KBArticle] = Field(default_factory=list)
    query: str = ""


# ──────────────────────────────────────────────
# REFUND SCHEMAS
# ──────────────────────────────────────────────

class RefundEligibilityInput(BaseModel):
    order_id: str

class RefundEligibilityOutput(BaseModel):
    eligible: bool = False
    reason: str = ""
    max_refund_amount: float = 0.0
    policy_reference: str = ""
    order_status: str = ""

class IssueRefundInput(BaseModel):
    order_id: str
    amount: float

    @validator("amount")
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError("Refund amount must be positive")
        return v

class IssueRefundOutput(BaseModel):
    success: bool = False
    transaction_id: str = ""
    refunded_amount: float = 0.0
    message: str = ""


# ──────────────────────────────────────────────
# COMMUNICATION SCHEMAS
# ──────────────────────────────────────────────

class SendReplyInput(BaseModel):
    ticket_id: str
    message: str

class SendReplyOutput(BaseModel):
    success: bool = False
    message_id: str = ""
    timestamp: str = ""

class EscalateInput(BaseModel):
    ticket_id: str
    summary: str
    priority: str = "medium"

class EscalateOutput(BaseModel):
    success: bool = True
    escalation_id: str = ""
    assigned_team: str = ""
    priority: str = ""


# ──────────────────────────────────────────────
# VALIDATION FUNCTIONS
# ──────────────────────────────────────────────

# Schema registry mapping tool names to their input/output models
TOOL_SCHEMAS = {
    "get_order": {"input": OrderInput, "output": OrderOutput},
    "get_customer": {"input": CustomerInput, "output": CustomerOutput},
    "get_product": {"input": ProductInput, "output": ProductOutput},
    "search_knowledge_base": {"input": KBSearchInput, "output": KBSearchOutput},
    "check_refund_eligibility": {"input": RefundEligibilityInput, "output": RefundEligibilityOutput},
    "issue_refund": {"input": IssueRefundInput, "output": IssueRefundOutput},
    "send_reply": {"input": SendReplyInput, "output": SendReplyOutput},
    "escalate": {"input": EscalateInput, "output": EscalateOutput},
}


def validate_tool_input(tool_name: str, params: dict) -> dict:
    """
    Validate tool input parameters against the schema.
    Returns validated (and possibly coerced) parameters.
    Raises ToolValidationError on failure.
    """
    schema = TOOL_SCHEMAS.get(tool_name, {}).get("input")
    if not schema:
        logger.warning(f"No input schema found for tool '{tool_name}'")
        return params

    try:
        validated = schema(**params)
        return validated.model_dump()
    except Exception as e:
        from utils.retry import ToolValidationError
        raise ToolValidationError(f"Input validation failed for {tool_name}: {e}")


def validate_tool_output(tool_name: str, data: Any) -> dict:
    """
    Validate tool output against the schema.
    Handles missing fields by filling defaults.
    Returns validated data dict, or flags issues.
    """
    schema = TOOL_SCHEMAS.get(tool_name, {}).get("output")
    if not schema:
        logger.warning(f"No output schema found for tool '{tool_name}'")
        return data if isinstance(data, dict) else {"raw": data}

    try:
        if isinstance(data, dict):
            validated = schema(**data)
        elif isinstance(data, list):
            # For tools that return lists (like KB search results)
            return {"articles": data} if tool_name == "search_knowledge_base" else {"items": data}
        else:
            return {"raw": data}

        result = validated.model_dump()

        # Flag missing critical fields
        warnings = []
        for field_name, field_info in schema.model_fields.items():
            if field_info.is_required() and not result.get(field_name):
                warnings.append(f"Missing required field: {field_name}")

        if warnings:
            result["_validation_warnings"] = warnings
            logger.warning(f"Validation warnings for {tool_name}: {warnings}")

        return result
    except Exception as e:
        logger.warning(f"Output validation partial failure for {tool_name}: {e}. Returning raw data.")
        return data if isinstance(data, dict) else {"raw": data, "_validation_error": str(e)}
