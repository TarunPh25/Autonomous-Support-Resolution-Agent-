"""
Agent Loop — implements the ReAct (Reason + Act) reasoning loop.

For each ticket:
    while not done:
        1. Analyze current state (Think)
        2. Decide next action and tool (Decide)
        3. Execute tool (Act)
        4. Update memory/context (Observe)
        5. Continue reasoning

Safety: Escalates if confidence < threshold, tool failures repeat,
or specific escalation triggers are met.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

from models.state import AgentState, ReasoningStep, TicketStatus
from models.ticket import Ticket
from agent.tool_registry import ToolRegistry
from agent.decision_engine import (
    DecisionEngine,
    classify_ticket,
    classify_ticket_with_llm,
    extract_order_id,
    generate_goals,
)
from utils.logger import AuditLogger

logger = logging.getLogger("agent.loop")


async def resolve_ticket(
    ticket_data: dict,
    registry: ToolRegistry,
    engine: DecisionEngine,
    output_dir: str = "output/audit_logs"
) -> dict:
    """
    Process a single support ticket through the full ReAct reasoning loop.
    
    Args:
        ticket_data: Raw ticket dict from tickets.json
        registry: Tool registry with all tools registered
        engine: Decision engine instance
        output_dir: Directory for audit logs
    
    Returns:
        Complete audit log dict
    """
    ticket_id = ticket_data.get("ticket_id", "UNKNOWN")
    audit = AuditLogger(ticket_id, output_dir)
    
    # ── Initialize state ──
    state = AgentState(
        ticket_id=ticket_id,
        ticket_data=ticket_data,
        start_time=time.time()
    )

    # ── Step 0: Classify and extract signals ──
    subject = ticket_data.get("subject", "")
    body = ticket_data.get("body", "")
    
    # HYBRID: Use LLM classification if engine has LLM enabled
    llm_reasoning = None
    if engine.use_llm and engine._llm_client:
        category, flags, class_confidence, llm_reasoning = await classify_ticket_with_llm(
            subject, body, engine._llm_client
        )
        if llm_reasoning:
            engine._record_llm_call(
                f"classify_ticket({ticket_id})",
                f"category={category}, reasoning={llm_reasoning}",
                True
            )
            audit.set_metadata("llm_classification_reasoning", llm_reasoning)
    else:
        category, flags, class_confidence = classify_ticket(subject, body)

    state.category = category
    state.flags = flags
    
    order_id = extract_order_id(f"{subject} {body}")
    if order_id:
        state.add_to_memory("extracted_order_id", order_id)
    
    audit.set_metadata("initial_category", category)
    audit.set_metadata("initial_flags", flags)
    audit.set_metadata("classification_confidence", class_confidence)
    audit.set_metadata("extracted_order_id", order_id)
    audit.set_metadata("mode", "llm" if engine.use_llm else "deterministic")
    
    logger.info(
        f"[{ticket_id}] Category: {category}, Flags: {flags}, "
        f"Order: {order_id or 'none'}, Class conf: {class_confidence:.2f}"
        f"{' [LLM]' if llm_reasoning else ' [DET]'}"
    )

    # ── Generate goals ──
    state.goals = generate_goals(state)
    goal_descriptions = [g.description for g in state.goals]
    audit.set_metadata("initial_goals", goal_descriptions)
    
    logger.info(f"[{ticket_id}] Generated {len(state.goals)} goals")

    # ── ReAct Loop ──
    while state.status == TicketStatus.IN_PROGRESS and state.current_step < state.max_steps:
        step_num = state.current_step + 1

        # ───── THINK ─────
        thought = await engine.analyze(state)

        # ───── DECIDE ─────
        tool_name, params, reason = await engine.decide_action(state)

        # ───── SAFETY: Pre-action checks ─────
        if tool_name == "issue_refund" and not state.refund_eligibility_checked:
            # Block refund if eligibility hasn't been checked
            thought += " [SAFETY] Cannot issue refund without eligibility check."
            tool_name = "check_refund_eligibility"
            oid = state.get_from_memory("extracted_order_id") or params.get("order_id", "")
            params = {"order_id": oid}
            reason = "Safety constraint: Must check refund eligibility before issuing refund."

        # ───── ACT ─────
        logger.info(f"[{ticket_id}] Step {step_num}: {tool_name}({params})")
        
        result = await registry.execute(tool_name, params)

        # ───── OBSERVE ─────
        observation = result.data if result.success else {"error": result.error}

        # Record step
        step = ReasoningStep(
            step=step_num,
            timestamp=datetime.now(timezone.utc).isoformat(),
            thought=thought,
            action=tool_name,
            action_input=params,
            observation=observation,
            success=result.success,
            reason=reason
        )
        state.steps.append(step)
        state.record_tool_call(tool_name, result.success)
        state.current_step = step_num

        # Audit log
        audit.add_step(
            step_num=step_num,
            thought=thought,
            action=tool_name,
            action_input=params,
            observation=observation,
            success=result.success,
            reason=reason,
            latency_ms=result.latency_ms,
            retries=result.retries
        )

        if not result.success:
            audit.add_error(
                error_type="tool_failure",
                message=result.error or "Unknown error",
                context=f"Tool: {tool_name}, Params: {params}"
            )

        # ── Update memory based on tool result ──
        if result.success:
            _update_memory(state, tool_name, result.data, engine)

        # ── Check terminal conditions ──

        # Escalation completed
        if tool_name == "escalate" and result.success:
            state.status = TicketStatus.ESCALATED
            state.resolution_message = params.get("summary", "Escalated to human agent")
            break

        # Reply sent (normal resolution)
        if tool_name == "send_reply" and result.success:
            # Check if this was a "needs info" reply
            if state.category == "unknown" or (
                not state.get_from_memory("order") and 
                state.category not in ("general_faq",) and
                state.get_from_memory("customer", {}).get("found") is False
            ):
                state.status = TicketStatus.NEEDS_INFO
            else:
                state.status = TicketStatus.RESOLVED
            state.resolution_message = params.get("message", "")
            break

        # Auto-escalate on repeated failures  
        if state.consecutive_failures >= 3:
            logger.warning(f"[{ticket_id}] 3 consecutive failures — forcing escalation")
            esc_summary = (
                f"Automated escalation for {ticket_id}: {state.consecutive_failures} "
                f"consecutive tool failures. Category: {category}. "
                f"Unable to gather required data to process ticket."
            )
            esc_result = await registry.execute("escalate", {
                "ticket_id": ticket_id,
                "summary": esc_summary,
                "priority": "high"
            })
            audit.add_step(
                step_num=step_num + 1,
                thought="Too many consecutive tool failures — must escalate to human agent.",
                action="escalate",
                action_input={"ticket_id": ticket_id, "summary": esc_summary, "priority": "high"},
                observation=esc_result.data if esc_result.success else {"error": esc_result.error},
                success=esc_result.success,
                reason="Automated escalation due to repeated tool failures"
            )
            state.status = TicketStatus.ESCALATED
            state.resolution_message = esc_summary
            break

    # ── Handle max steps reached ──
    if state.status == TicketStatus.IN_PROGRESS:
        logger.warning(f"[{ticket_id}] Max steps reached without resolution — escalating")
        state.status = TicketStatus.FAILED
        esc_result = await registry.execute("escalate", {
            "ticket_id": ticket_id,
            "summary": f"Agent reached max steps ({state.max_steps}) without resolution. Category: {category}.",
            "priority": "medium"
        })
        if esc_result.success:
            state.status = TicketStatus.ESCALATED

    # ── Final confidence ──
    confidence, confidence_reason = engine.estimate_confidence(state)
    state.confidence = confidence
    state.confidence_reason = confidence_reason

    # Collect policy references
    policy_refs = []
    policy_data = state.get_from_memory("policy", {})
    if isinstance(policy_data, dict):
        for article in policy_data.get("articles", []):
            title = article.get("title", "")
            if title:
                policy_refs.append(title)
    elig_data = state.get_from_memory("refund_eligibility", {})
    if isinstance(elig_data, dict) and elig_data.get("policy_reference"):
        policy_refs.append(elig_data["policy_reference"])
    state.policy_references = list(set(policy_refs))

    # ── Finalize audit log ──
    # Include LLM usage data if applicable
    llm_data = {}
    if engine.use_llm:
        llm_data = {
            "llm_used": True,
            "llm_calls": engine.llm_audit_trail,
            "llm_stats": engine._llm_client.stats if engine._llm_client else {},
        }
    else:
        llm_data = {"llm_used": False}
    audit.set_metadata("llm", llm_data)

    audit_record = audit.finalize(
        status=state.status.value,
        confidence=confidence,
        confidence_reason=confidence_reason,
        policy_references=state.policy_references,
        resolution_message=state.resolution_message[:500] if state.resolution_message else "",
        category=state.category or "unknown",
        customer_tier=state.customer_tier or "unknown",
        flags=state.flags,
    )

    logger.info(
        f"[{ticket_id}] ── DONE: {state.status.value} | "
        f"Confidence: {confidence:.2f} | Steps: {state.current_step} | "
        f"Tools: {state.total_tool_calls}"
    )

    return audit_record


def _update_memory(state: AgentState, tool_name: str, data: dict, engine: DecisionEngine):
    """Update agent memory and goal satisfaction based on tool results."""
    
    if tool_name == "get_customer":
        if data.get("found", True) and "error" not in data:
            state.add_to_memory("customer", data)
            state.customer_tier = data.get("tier", "standard")
            # Satisfy the goal
            state.satisfy_goal("get_customer_profile")
            state.satisfy_goal("lookup_customer_for_order")
        else:
            state.add_to_memory("customer", data)
            state.satisfy_goal("get_customer_profile")
            state.satisfy_goal("lookup_customer_for_order")

    elif tool_name == "get_order":
        if data.get("found", True) and "error" not in data:
            state.add_to_memory("order", data)
            state.satisfy_goal("get_order_details")
            state.satisfy_goal("check_shipping")
            
            # Now we can fill in the product goal params
            product_id = data.get("product_id")
            if product_id:
                for goal in state.goals:
                    if goal.goal_id == "get_product_info" and not goal.required_params.get("product_id"):
                        goal.required_params = {"product_id": product_id}
        else:
            state.add_to_memory("order", data)
            state.satisfy_goal("get_order_details")
            state.satisfy_goal("check_shipping")

    elif tool_name == "get_product":
        if data.get("found", True):
            state.add_to_memory("product", data)
        state.satisfy_goal("get_product_info")

    elif tool_name == "search_knowledge_base":
        state.add_to_memory("policy", data)
        state.satisfy_goal("search_policy")
        state.satisfy_goal("search_faq")
        
        # Add policy references
        articles = data.get("articles", []) if isinstance(data, dict) else []
        for article in articles:
            title = article.get("title", "")
            if title and title not in state.policy_references:
                state.policy_references.append(title)

    elif tool_name == "check_refund_eligibility":
        state.add_to_memory("refund_eligibility", data)
        state.refund_eligibility_checked = True
        state.refund_eligible = data.get("eligible", False)
        state.refund_max_amount = data.get("max_refund_amount", 0)
        state.satisfy_goal("check_refund_eligibility")
        
        # If eligible and amount is <= $200, add issue_refund goal
        if data.get("eligible") and state.refund_max_amount <= 200:
            order_id = state.get_from_memory("extracted_order_id", "")
            if order_id and state.category in ("refund", "return", "damage_claim", "cancellation"):
                # Check if we don't already have this goal
                existing_ids = {g.goal_id for g in state.goals}
                if "issue_refund_action" not in existing_ids:
                    from models.state import InformationGoal
                    state.goals.append(InformationGoal(
                        goal_id="issue_refund_action",
                        description=f"Issue refund of ${state.refund_max_amount:.2f} for {order_id}",
                        priority=0.8,
                        required_tool="issue_refund",
                        required_params={"order_id": order_id, "amount": state.refund_max_amount},
                        depends_on=["check_refund_eligibility"],
                        result_key="refund_result"
                    ))

    elif tool_name == "issue_refund":
        state.add_to_memory("refund_result", data)
        state.satisfy_goal("issue_refund_action")

    elif tool_name == "send_reply":
        state.satisfy_goal("send_final_response")

    elif tool_name == "escalate":
        # Satisfy all remaining goals
        for goal in state.goals:
            goal.satisfied = True
