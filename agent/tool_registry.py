"""
Tool Registry — central registry for all tools with validation and retry wrapping.
Maps tool names to async callables with input/output schema enforcement.
"""

import time
import logging
from typing import Callable, Any

from utils.retry import retry_with_backoff, ToolTimeoutError, ToolServiceError, ToolValidationError
from utils.validator import validate_tool_input, validate_tool_output
from models.state import ToolResult

logger = logging.getLogger("agent.registry")


class ToolRegistry:
    """
    Central registry that maps tool names to their implementations.
    Wraps every tool call with:
    1. Input validation (Pydantic)
    2. Retry with exponential backoff
    3. Output validation
    4. Latency measurement
    """

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, func: Callable, description: str = ""):
        """Register a tool by name."""
        self._tools[name] = func
        self._descriptions[name] = description
        logger.info(f"Registered tool: {name}")

    def get_tool_names(self) -> list[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    def get_tool_descriptions(self) -> dict[str, str]:
        """Get tool name → description mapping."""
        return dict(self._descriptions)

    async def execute(self, name: str, params: dict, max_retries: int = 2) -> ToolResult:
        """
        Execute a tool with full validation and retry wrapping.
        
        Flow:
        1. Validate input params against schema
        2. Call tool function with retry/backoff
        3. Validate output against schema
        4. Return structured ToolResult
        """
        if name not in self._tools:
            return ToolResult(
                success=False,
                error=f"Unknown tool: {name}. Available: {list(self._tools.keys())}",
                latency_ms=0
            )

        start = time.time()

        # Step 1: Validate inputs
        try:
            validated_params = validate_tool_input(name, params)
        except ToolValidationError as e:
            return ToolResult(
                success=False,
                error=f"Input validation failed: {e}",
                latency_ms=(time.time() - start) * 1000
            )

        # Step 2: Execute with retry
        func = self._tools[name]
        retry_result = await retry_with_backoff(
            func,
            **validated_params,
            max_retries=max_retries,
            retryable_exceptions=(ToolTimeoutError, ToolServiceError)
        )

        latency = (time.time() - start) * 1000

        if not retry_result["success"]:
            return ToolResult(
                success=False,
                error=retry_result.get("error", "Tool execution failed after retries"),
                latency_ms=latency,
                retries=retry_result.get("retries", 0)
            )

        raw_result = retry_result["result"]

        # Step 3: Validate output
        try:
            validated_output = validate_tool_output(name, raw_result)
        except Exception as e:
            logger.warning(f"Output validation issue for {name}: {e}. Using raw result.")
            validated_output = raw_result if isinstance(raw_result, dict) else {"raw": raw_result}

        return ToolResult(
            success=True,
            data=validated_output,
            latency_ms=latency,
            retries=retry_result.get("retries", 0)
        )


def create_default_registry() -> ToolRegistry:
    """
    Create and populate the default tool registry with all available tools.
    """
    from tools.order import get_order
    from tools.customer import get_customer
    from tools.product import get_product
    from tools.kb import search_knowledge_base
    from tools.refund import check_refund_eligibility, issue_refund
    from tools.communication import send_reply, escalate

    registry = ToolRegistry()

    # READ tools
    registry.register("get_order", get_order,
                       "Retrieve order details by order_id. Returns status, dates, amounts.")
    registry.register("get_customer", get_customer,
                       "Retrieve customer profile by email. Returns tier, history, notes.")
    registry.register("get_product", get_product,
                       "Retrieve product metadata by product_id. Returns warranty, return window.")
    registry.register("search_knowledge_base", search_knowledge_base,
                       "Search ShopWave policies by query. Returns relevant policy articles.")

    # ACTION tools
    registry.register("check_refund_eligibility", check_refund_eligibility,
                       "Check if an order is eligible for refund. Returns eligibility and reason.")
    registry.register("issue_refund", issue_refund,
                       "Issue a refund for an order. IRREVERSIBLE. Must check eligibility first.")
    registry.register("send_reply", send_reply,
                       "Send a reply message to the customer for a ticket.")
    registry.register("escalate", escalate,
                       "Escalate ticket to human agent with summary and priority.")

    return registry
