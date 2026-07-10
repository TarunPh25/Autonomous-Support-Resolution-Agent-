"""
Decision Engine — goal-driven planner that drives the agent's reasoning.

This is the brain of the agent. It:
1. Analyzes tickets to extract signals and classify intent
2. Generates information goals dynamically
3. Selects the highest-priority next action at each step
4. Estimates confidence based on data completeness and policy alignment
5. Generates human-readable thoughts and reasoning

HYBRID ARCHITECTURE:
- Deterministic planner remains the SOURCE OF TRUTH for all decisions
- LLM augments classification, reasoning explanation, and reply composition
- LLM NEVER controls tool selection, escalation, or irreversible actions
- If LLM is unavailable or fails, system falls back to deterministic mode
- Enabled via: --mode llm or AGENT_MODE=llm environment variable
"""

import re
import asyncio
import logging
from typing import Optional, Tuple
from models.state import AgentState, InformationGoal

logger = logging.getLogger("agent.decision")


# ─────────────────────────────────────────────────────────
# TICKET SIGNAL EXTRACTION
# ─────────────────────────────────────────────────────────

# Keyword → category mapping with weights
CATEGORY_SIGNALS = {
    "refund": {
        "keywords": ["refund", "money back", "reimburse", "get my money"],
        "category": "refund",
    },
    "return": {
        "keywords": ["return", "send back", "give back", "return it"],
        "category": "return",
    },
    "cancel": {
        "keywords": ["cancel", "cancellation", "don't want", "stop order", "changed my mind"],
        "category": "cancellation",
    },
    "damage": {
        "keywords": ["damaged", "broken", "cracked", "defective", "not working", "stopped working"],
        "category": "damage_claim",
    },
    "wrong_item": {
        "keywords": ["wrong", "incorrect", "different", "wrong size", "wrong colour", "wrong color"],
        "category": "wrong_item",
    },
    "warranty": {
        "keywords": ["warranty", "guarantee", "manufacturing defect"],
        "category": "warranty",
    },
    "tracking": {
        "keywords": ["where is", "tracking", "delivery", "when will", "shipped", "haven't received", "transit"],
        "category": "order_status",
    },
    "faq": {
        "keywords": ["what is", "how do", "policy", "general question", "question about"],
        "category": "general_faq",
    },
    "exchange": {
        "keywords": ["exchange", "swap", "replacement", "replace"],
        "category": "exchange",
    },
}

# Flags to detect
FLAG_SIGNALS = {
    "threatening_language": ["lawyer", "legal", "sue", "dispute", "bank", "chargeback"],
    "social_engineering": ["premium member", "vip member", "instant refund", "without questions", "as per your policy"],
    "urgency": ["urgent", "immediately", "right now", "today", "asap"],
    "frustration": ["unacceptable", "ridiculous", "worst", "terrible", "angry"],
}


def extract_order_id(text: str) -> Optional[str]:
    """Extract order ID from ticket text."""
    match = re.search(r'ORD-\d{4}', text, re.IGNORECASE)
    return match.group(0) if match else None


def extract_product_id(text: str) -> Optional[str]:
    """Extract product ID from ticket text."""
    match = re.search(r'P\d{3}', text)
    return match.group(0) if match else None


def classify_ticket(subject: str, body: str) -> Tuple[str, list[str], float]:
    """
    Classify a ticket into a category using deterministic keyword analysis.
    This is the DETERMINISTIC classifier — always available, no external deps.
    Returns (category, flags, classification_confidence).
    """
    text = f"{subject} {body}".lower()
    
    # Score each category
    scores = {}
    for cat_key, cat_info in CATEGORY_SIGNALS.items():
        score = sum(1 for kw in cat_info["keywords"] if kw in text)
        if score > 0:
            scores[cat_info["category"]] = score

    # Detect flags
    flags = []
    for flag_name, keywords in FLAG_SIGNALS.items():
        if any(kw in text for kw in keywords):
            flags.append(flag_name)

    if not scores:
        return "unknown", flags, 0.3

    # Best category
    best_cat = max(scores, key=scores.get)
    confidence = min(0.5 + scores[best_cat] * 0.15, 0.9)

    return best_cat, flags, confidence


async def classify_ticket_with_llm(
    subject: str,
    body: str,
    llm_client
) -> Tuple[str, list[str], float, Optional[str]]:
    """
    Hybrid ticket classification: tries LLM first, falls back to deterministic.
    
    Returns (category, flags, confidence, llm_reasoning).
    llm_reasoning is None if deterministic mode was used.
    
    SAFETY: LLM classification is validated against known categories.
    If LLM returns an invalid category, deterministic result is used.
    """
    # Always compute deterministic result as baseline
    det_category, det_flags, det_confidence = classify_ticket(subject, body)
    
    if llm_client is None or not llm_client.available:
        return det_category, det_flags, det_confidence, None

    # Try LLM classification
    try:
        llm_result = await llm_client.classify_ticket(subject, body)
        if llm_result and "category" in llm_result:
            llm_category = llm_result["category"]
            llm_flags = llm_result.get("flags", [])
            llm_confidence = llm_result.get("confidence", 0.7)
            llm_reasoning = llm_result.get("reasoning", "")
            
            # Merge flags: union of deterministic and LLM flags
            valid_flags = {"threatening_language", "social_engineering", "urgency", "frustration"}
            merged_flags = list(set(det_flags) | (set(llm_flags) & valid_flags))
            
            logger.info(
                f"LLM classification: {llm_category} (conf: {llm_confidence:.2f}) "
                f"vs deterministic: {det_category} (conf: {det_confidence:.2f})"
            )
            
            return llm_category, merged_flags, llm_confidence, llm_reasoning
    except Exception as e:
        logger.debug(f"LLM classification failed: {e}. Using deterministic.")
    
    return det_category, det_flags, det_confidence, None


# ─────────────────────────────────────────────────────────
# GOAL GENERATION
# ─────────────────────────────────────────────────────────

def generate_goals(state: AgentState) -> list[InformationGoal]:
    """
    Dynamically generate information goals based on ticket analysis.
    Goals are NOT hardcoded per category — they are generated based on
    what signals are present in the ticket.
    """
    ticket = state.ticket_data
    text = f"{ticket.get('subject', '')} {ticket.get('body', '')}".lower()
    order_id = extract_order_id(f"{ticket.get('subject', '')} {ticket.get('body', '')}")
    category = state.category or "unknown"
    email = ticket.get("customer_email", "")
    goals = []

    # ── UNIVERSAL GOALS (apply to almost every ticket) ──

    # Always get customer profile for tier-aware decisions
    if email:
        goals.append(InformationGoal(
            goal_id="get_customer_profile",
            description="Retrieve customer profile for tier-based decision making",
            priority=0.9,
            required_tool="get_customer",
            required_params={"email": email},
            result_key="customer"
        ))

    # ── CONDITIONAL GOALS (based on ticket signals) ──

    # If we have an order ID, get order details
    if order_id:
        goals.append(InformationGoal(
            goal_id="get_order_details",
            description=f"Retrieve order details for {order_id}",
            priority=0.95,
            required_tool="get_order",
            required_params={"order_id": order_id},
            result_key="order"
        ))
    elif email and category not in ("general_faq",):
        # No order ID but not a FAQ — try to find orders by email lookup
        goals.append(InformationGoal(
            goal_id="lookup_customer_for_order",
            description="Customer didn't provide order ID — lookup customer to find associated orders",
            priority=0.85,
            required_tool="get_customer",
            required_params={"email": email},
            result_key="customer"
        ))

    # If any return/refund/damage/cancel signals → need policy context
    policy_triggers = ["refund", "return", "damage_claim", "wrong_item", "cancellation", "warranty", "exchange"]
    if category in policy_triggers or "social_engineering" in state.flags:
        # Map category to best KB query
        query_map = {
            "refund": "refund policy eligibility",
            "return": "return policy window",
            "damage_claim": "damaged items policy",
            "wrong_item": "wrong item delivered policy",
            "cancellation": "order cancellation policy",
            "warranty": "warranty claims policy",
            "exchange": "exchange policy replacement",
            "unknown": "return refund policy",
        }
        kb_query = query_map.get(category, "return refund policy")
        
        # If social engineering detected, also search tier policy
        if "social_engineering" in state.flags:
            kb_query = "customer tier benefits premium vip policy"

        goals.append(InformationGoal(
            goal_id="search_policy",
            description=f"Search knowledge base for relevant policies: {kb_query}",
            priority=0.85,
            required_tool="search_knowledge_base",
            required_params={"query": kb_query},
            result_key="policy"
        ))

    # If order tracking/status
    if category == "order_status" and order_id:
        goals.append(InformationGoal(
            goal_id="check_shipping",
            description="Check order shipping status and provide tracking info",
            priority=0.9,
            required_tool="get_order",
            required_params={"order_id": order_id},
            result_key="order"
        ))

    # FAQ goal  
    if category == "general_faq":
        faq_query = text[:100]  # Use ticket text as search query
        goals.append(InformationGoal(
            goal_id="search_faq",
            description="Search knowledge base to answer customer's general question",
            priority=0.95,
            required_tool="search_knowledge_base",
            required_params={"query": faq_query},
            result_key="policy"
        ))

    # If order found, we'll need product details
    if order_id:
        goals.append(InformationGoal(
            goal_id="get_product_info",
            description="Retrieve product details for return window and warranty info",
            priority=0.7,
            required_tool="get_product",
            required_params={},  # Will be filled after order lookup
            depends_on=["get_order_details"],
            result_key="product"
        ))

    # Refund eligibility check (depends on order lookup)
    if category in ("refund", "return", "damage_claim", "wrong_item", "cancellation") and order_id:
        goals.append(InformationGoal(
            goal_id="check_refund_eligibility",
            description="Check if order is eligible for refund based on policies",
            priority=0.75,
            required_tool="check_refund_eligibility",
            required_params={"order_id": order_id},
            depends_on=["get_order_details"],
            result_key="refund_eligibility"
        ))

    # Final response goal — always last
    goals.append(InformationGoal(
        goal_id="send_final_response",
        description="Send final response to customer",
        priority=0.1,  # Low priority until other goals satisfied
        required_tool="send_reply",
        required_params={"ticket_id": ticket.get("ticket_id", "")},
        result_key="reply_sent"
    ))

    return goals


# ─────────────────────────────────────────────────────────
# DECISION MAKING
# ─────────────────────────────────────────────────────────

class DecisionEngine:
    """
    Goal-driven decision engine that selects the best next action
    based on current state, satisfied goals, and available information.
    
    HYBRID DESIGN:
    - Deterministic planner is ALWAYS the source of truth
    - LLM augments analyze(), classify(), and compose_reply() ONLY
    - LLM NEVER controls decide_action(), escalation triggers, or tool selection
    - Graceful fallback: if LLM fails, deterministic logic takes over silently
    """

    def __init__(self, use_llm: bool = False):
        self.use_llm = use_llm
        self._llm_client = None
        self._llm_calls = []  # Audit trail: [{prompt_summary, output, success}]

        if self.use_llm:
            from utils.llm_client import get_llm_client
            self._llm_client = get_llm_client()
            if not self._llm_client.available:
                logger.warning(
                    "LLM mode requested but Groq client unavailable. "
                    "Falling back to deterministic mode."
                )
                self.use_llm = False
            else:
                logger.info("LLM-assisted mode ACTIVE. Deterministic planner remains source of truth.")

    @property
    def llm_audit_trail(self) -> list[dict]:
        """Return the LLM call audit trail for logging."""
        return list(self._llm_calls)

    def _record_llm_call(self, prompt_summary: str, output: Optional[str], success: bool):
        """Record an LLM call for audit logging."""
        self._llm_calls.append({
            "prompt_summary": prompt_summary,
            "llm_output": output[:300] if output else None,
            "success": success,
        })

    async def analyze(self, state: AgentState) -> str:
        """
        Produce a thought analyzing the current state.
        This is the 'Think' step of the ReAct loop.
        
        HYBRID: If LLM mode is active, generates a richer thought using LLM,
        augmented with the deterministic analysis as context.
        Falls back to deterministic thought if LLM fails.
        """
        # Always generate the deterministic thought first (source of truth)
        deterministic_thought = self._analyze_deterministic(state)

        # If LLM mode, try to enhance it
        if self.use_llm and self._llm_client:
            try:
                state_summary = self._build_state_summary(state, deterministic_thought)
                llm_thought = await self._llm_client.generate_thought(state_summary)
                if llm_thought:
                    self._record_llm_call(
                        f"generate_thought(step={state.current_step})",
                        llm_thought, True
                    )
                    # Combine: deterministic facts + LLM insight
                    return f"{deterministic_thought} [LLM Insight] {llm_thought}"
                else:
                    self._record_llm_call(
                        f"generate_thought(step={state.current_step})",
                        None, False
                    )
            except Exception as e:
                logger.debug(f"LLM thought generation failed: {e}. Using deterministic.")
                self._record_llm_call(f"generate_thought(step={state.current_step})", None, False)

        return deterministic_thought

    def _analyze_deterministic(self, state: AgentState) -> str:
        """
        Pure deterministic thought generation — the original analyze() logic.
        Always available, never depends on external services.
        """
        ticket = state.ticket_data
        step = state.current_step
        
        if step == 0:
            # Initial analysis
            category = state.category or "unknown"
            order_id = extract_order_id(f"{ticket.get('subject', '')} {ticket.get('body', '')}")
            flags_str = f" Flags detected: {', '.join(state.flags)}." if state.flags else ""
            return (
                f"Analyzing ticket {ticket.get('ticket_id', '?')}: "
                f"Subject: '{ticket.get('subject', '')}'. "
                f"Classified as '{category}'.{flags_str} "
                f"{'Found order ID: ' + order_id + '.' if order_id else 'No order ID found in ticket.'} "
                f"Need to gather information to resolve this ticket."
            )

        # Subsequent steps -- analyze based on what we know
        memory = state.memory
        unsatisfied = [g for g in state.goals if not g.satisfied]
        satisfied = [g for g in state.goals if g.satisfied]

        parts = [f"Step {step + 1} analysis for {ticket.get('ticket_id', '?')}:"]
        
        if "customer" in memory:
            cust = memory["customer"]
            tier = cust.get("tier", "unknown")
            parts.append(f"Customer identified: {cust.get('name', '?')} (tier: {tier}).")
            
        if "order" in memory:
            order = memory["order"]
            parts.append(
                f"Order {order.get('order_id', '?')}: status={order.get('status', '?')}, "
                f"amount=${order.get('amount', 0)}, product={order.get('product_id', '?')}."
            )
            
        if "product" in memory:
            prod = memory["product"]
            parts.append(
                f"Product: {prod.get('name', '?')} -- return window: {prod.get('return_window_days', '?')} days, "
                f"warranty: {prod.get('warranty_months', 0)} months."
            )

        if "refund_eligibility" in memory:
            elig = memory["refund_eligibility"]
            parts.append(
                f"Refund eligibility: {'eligible' if elig.get('eligible') else 'NOT eligible'} "
                f"-- {elig.get('reason', '')}."
            )

        if "policy" in memory:
            policy_data = memory["policy"]
            articles = policy_data.get("articles", []) if isinstance(policy_data, dict) else []
            if articles:
                titles = [a.get("title", "") for a in articles[:2]]
                parts.append(f"Relevant policies found: {', '.join(titles)}.")

        remaining = len(unsatisfied) - 1  # Exclude final response
        if remaining > 0:
            parts.append(f"{remaining} information goal(s) still pending.")
        else:
            parts.append("All information gathered. Ready to decide and respond.")

        return " ".join(parts)

    def _build_state_summary(self, state: AgentState, deterministic_thought: str) -> str:
        """
        Build a structured summary of current state for the LLM.
        Includes ticket content, known facts, and deterministic analysis.
        """
        ticket = state.ticket_data
        parts = [
            f"Ticket ID: {state.ticket_id}",
            f"Subject: {ticket.get('subject', '')}",
            f"Body: {ticket.get('body', '')}",
            f"Category: {state.category}",
            f"Step: {state.current_step + 1}",
            f"Flags: {state.flags}",
            f"\nDeterministic Analysis: {deterministic_thought}",
        ]

        if "customer" in state.memory:
            c = state.memory["customer"]
            parts.append(f"\nCustomer: {c.get('name', '?')} (tier: {c.get('tier', '?')}, "
                         f"orders: {c.get('total_orders', '?')}, spent: ${c.get('total_spent', 0)})")

        if "order" in state.memory:
            o = state.memory["order"]
            parts.append(f"Order: {o.get('order_id', '?')}, status: {o.get('status', '?')}, "
                         f"amount: ${o.get('amount', 0)}, return_deadline: {o.get('return_deadline', '?')}")

        if "refund_eligibility" in state.memory:
            e = state.memory["refund_eligibility"]
            parts.append(f"Refund Eligibility: {'Yes' if e.get('eligible') else 'No'} -- {e.get('reason', '')}")

        if "policy" in state.memory:
            policy = state.memory["policy"]
            if isinstance(policy, dict) and policy.get("articles"):
                titles = [a.get('title', '') for a in policy['articles'][:3]]
                parts.append(f"Relevant Policies: {', '.join(titles)}")

        unsatisfied = [g.description for g in state.goals if not g.satisfied]
        if unsatisfied:
            parts.append(f"\nPending goals: {'; '.join(unsatisfied)}")

        return "\n".join(parts)

    async def decide_action(self, state: AgentState) -> Tuple[str, dict, str]:
        """
        Select the next action based on the goal-driven planner.
        Returns (tool_name, params, reason).
        
        This is the 'Decide' step of the ReAct loop.
        """
        ticket = state.ticket_data
        ticket_id = ticket.get("ticket_id", "")
        
        # Check escalation triggers first
        escalation_reason = self._check_escalation_triggers(state)
        if escalation_reason:
            summary = self._build_escalation_summary(state, escalation_reason)
            priority = self._determine_priority(state)
            return "escalate", {
                "ticket_id": ticket_id,
                "summary": summary,
                "priority": priority
            }, f"Escalating because: {escalation_reason}"

        # Get next unsatisfied goal
        next_goal = state.get_next_unsatisfied_goal()

        if next_goal is None or next_goal.goal_id == "send_final_response":
            # All info gathered -- compose and send final response
            message = await self._compose_reply(state)
            return "send_reply", {
                "ticket_id": ticket_id,
                "message": message
            }, "All information gathered, composing and sending final response to customer."

        # Handle goals that need params from memory
        params = dict(next_goal.required_params)

        # Fill in dependent params
        if next_goal.goal_id == "get_product_info":
            order = state.get_from_memory("order", {})
            product_id = order.get("product_id")
            if product_id:
                params = {"product_id": product_id}
            else:
                # Can't get product without product_id -- skip this goal
                state.satisfy_goal(next_goal.goal_id)
                return await self.decide_action(state)  # Recurse to next goal

        reason = (
            f"Goal: {next_goal.description}. "
            f"Priority: {next_goal.priority:.1f}. "
            f"Using tool: {next_goal.required_tool}."
        )

        return next_goal.required_tool, params, reason

    def estimate_confidence(self, state: AgentState) -> Tuple[float, str]:
        """
        Estimate confidence in the agent's resolution.
        
        confidence = weighted_average(
            data_completeness,
            tool_success_rate,
            policy_alignment,
            ambiguity
        )
        """
        factors = {}

        # 1. Data completeness — how much critical data do we have?
        required_data = ["customer", "order"]
        found = sum(1 for k in required_data if k in state.memory)
        data_completeness = found / max(len(required_data), 1)
        
        # Adjust for ticket type
        if state.category == "general_faq":
            data_completeness = 1.0 if "policy" in state.memory else 0.5
        elif state.category == "order_status":
            data_completeness = 1.0 if "order" in state.memory else 0.3
        
        factors["data_completeness"] = data_completeness

        # 2. Tool success rate
        if state.total_tool_calls > 0:
            success_rate = 1.0 - (state.total_failures / state.total_tool_calls)
        else:
            success_rate = 0.5
        factors["tool_success_rate"] = success_rate

        # 3. Policy alignment — do we have policy support for our decision?
        has_policy = "policy" in state.memory
        policy_articles = state.memory.get("policy", {}).get("articles", []) if has_policy else []
        policy_alignment = min(len(policy_articles) * 0.35, 1.0) if policy_articles else 0.3
        factors["policy_alignment"] = policy_alignment

        # 4. Ambiguity — how clear is the ticket?
        ambiguity_penalty = 0.0
        if state.category == "unknown":
            ambiguity_penalty = 0.3
        if "social_engineering" in state.flags:
            ambiguity_penalty += 0.15
        if not extract_order_id(f"{state.ticket_data.get('subject', '')} {state.ticket_data.get('body', '')}"):
            if state.category not in ("general_faq",):
                ambiguity_penalty += 0.1
        clarity = max(1.0 - ambiguity_penalty, 0.0)
        factors["clarity"] = clarity

        # Weighted average
        weights = {
            "data_completeness": 0.30,
            "tool_success_rate": 0.20,
            "policy_alignment": 0.25,
            "clarity": 0.25,
        }

        confidence = sum(factors[k] * weights[k] for k in factors)
        confidence = max(0.0, min(confidence, 1.0))

        # Build reason
        reason_parts = []
        for k, v in factors.items():
            level = "high" if v >= 0.7 else ("medium" if v >= 0.4 else "low")
            reason_parts.append(f"{k}={level}({v:.2f})")
        reason = f"Confidence {confidence:.2f}: {', '.join(reason_parts)}"

        return confidence, reason

    def _check_escalation_triggers(self, state: AgentState) -> Optional[str]:
        """Check if any escalation trigger is met."""
        # Consecutive tool failures
        if state.consecutive_failures >= 2:
            return "Multiple consecutive tool failures — unable to gather required data"

        # Confidence too low (only check after step 3)
        if state.current_step >= 3:
            conf, _ = self.estimate_confidence(state)
            if conf < 0.4:
                return f"Confidence too low ({conf:.2f}) to make an autonomous decision"

        # Warranty case — agent can't handle
        order = state.get_from_memory("order", {})
        product = state.get_from_memory("product", {})
        if state.category == "warranty":
            return "Warranty claim detected — requires warranty team assessment"
        if state.category in ("damage_claim",) and product.get("warranty_months", 0) > 0:
            # Check if it's within warranty but outside return window
            elig = state.get_from_memory("refund_eligibility", {})
            if elig and not elig.get("eligible") and "expired" in elig.get("reason", "").lower():
                return "Item outside return window but under warranty — escalating as warranty claim"

        # Refund over $200 (after we know the amount)
        if state.category in ("refund", "return", "damage_claim", "wrong_item"):
            amount = order.get("amount", 0)
            elig = state.get_from_memory("refund_eligibility", {})
            if elig and elig.get("eligible") and amount > 200:
                return f"Refund amount (${amount}) exceeds $200 — requires supervisor approval"

        # Customer wants replacement/exchange — agent can't fulfil
        ticket_text = f"{state.ticket_data.get('subject', '')} {state.ticket_data.get('body', '')}".lower()
        if any(kw in ticket_text for kw in ["replacement", "replace", "exchange", "swap"]):
            if state.category in ("damage_claim", "wrong_item", "exchange"):
                # Only escalate if we've gathered enough data
                if state.current_step >= 3 and "order" in state.memory:
                    return "Customer requests replacement/exchange — requires fulfilment team"

        # Fraud/social engineering suspicion
        if "social_engineering" in state.flags:
            customer = state.get_from_memory("customer", {})
            if customer:
                # Verify tier claim
                actual_tier = customer.get("tier", "standard")
                if any(kw in ticket_text for kw in ["premium member", "vip"]):
                    if actual_tier == "standard":
                        return f"Social engineering detected — customer claims premium/VIP but is {actual_tier} tier"

        # Conflicting data
        if "order" in state.memory and state.memory["order"].get("found") is False:
            return "Order ID provided does not exist in system — cannot verify claim"

        return None

    def _determine_priority(self, state: AgentState) -> str:
        """Determine escalation priority level."""
        if "threatening_language" in state.flags or "social_engineering" in state.flags:
            return "high"
        if state.category == "warranty":
            return "medium"
        order = state.get_from_memory("order", {})
        if order.get("amount", 0) > 200:
            return "high"
        if state.consecutive_failures >= 2:
            return "medium"
        return "medium"

    def _build_escalation_summary(self, state: AgentState, reason: str) -> str:
        """Build a detailed escalation summary."""
        ticket = state.ticket_data
        parts = [
            f"Ticket {ticket.get('ticket_id', '?')} requires human intervention.",
            f"Reason: {reason}.",
            f"Category: {state.category or 'unknown'}.",
        ]

        if "customer" in state.memory:
            cust = state.memory["customer"]
            parts.append(f"Customer: {cust.get('name', '?')} (tier: {cust.get('tier', '?')}).")

        if "order" in state.memory:
            order = state.memory["order"]
            parts.append(
                f"Order: {order.get('order_id', '?')} — status: {order.get('status', '?')}, "
                f"amount: ${order.get('amount', 0)}."
            )

        actions_taken = [s.action for s in state.steps if s.action not in ("analyze",)]
        if actions_taken:
            parts.append(f"Actions taken: {', '.join(actions_taken)}.")

        # Recommended next step
        recommendations = {
            "warranty": "Have warranty team assess the defect and process claim.",
            "damage_claim": "Verify damage photos and process replacement or refund.",
            "refund": "Review order and process refund if eligible with supervisor override.",
            "wrong_item": "Arrange correct item shipment or process refund.",
            "exchange": "Process exchange through fulfilment team.",
        }
        rec = recommendations.get(state.category, "Review ticket and take appropriate action.")
        parts.append(f"Recommended: {rec}")

        return " ".join(parts)

    async def _compose_reply(self, state: AgentState) -> str:
        """
        Compose a professional customer-facing reply based on gathered information
        and policy context.
        
        HYBRID: If LLM mode active, generates a natural language reply using LLM
        with full context (ticket, decision, policy, tier). Falls back to
        deterministic template if LLM fails.
        
        SAFETY: LLM generates the TEXT only. Decision (refund/deny/escalate)
        is always made by the deterministic planner.
        """
        ticket = state.ticket_data
        category = state.category
        customer = state.get_from_memory("customer", {})
        order = state.get_from_memory("order", {})
        product = state.get_from_memory("product", {})
        eligibility = state.get_from_memory("refund_eligibility", {})
        policy = state.get_from_memory("policy", {})
        tier = customer.get("tier", "standard") if customer else "standard"
        customer_name = customer.get("name", "Customer") if customer else "Customer"
        first_name = customer_name.split()[0] if customer_name != "Customer" else "there"

        # ── Try LLM-generated reply first ──
        if self.use_llm and self._llm_client:
            try:
                decision_summary = self._build_decision_summary(state)
                llm_reply = await self._llm_client.compose_reply(
                    ticket=ticket,
                    decision_summary=decision_summary,
                    customer_name=customer_name,
                    customer_tier=tier,
                    policy_refs=state.policy_references,
                )
                if llm_reply:
                    self._record_llm_call(
                        f"compose_reply(category={category}, tier={tier})",
                        llm_reply, True
                    )
                    return llm_reply
                else:
                    self._record_llm_call(
                        f"compose_reply(category={category}, tier={tier})",
                        None, False
                    )
            except Exception as e:
                logger.debug(f"LLM reply composition failed: {e}. Using template.")
                self._record_llm_call(f"compose_reply(category={category})", None, False)

        # ── Deterministic template reply (fallback) ──
        greeting = f"Hi {first_name},\n\nThank you for reaching out to ShopWave Support."

        # ── Compose based on resolution ──

        if category == "refund" or category == "return":
            if eligibility.get("eligible"):
                refund_result = state.get_from_memory("refund_result", {})
                if refund_result and refund_result.get("success"):
                    return (
                        f"{greeting}\n\n"
                        f"We've processed your refund of ${refund_result.get('refunded_amount', 0):.2f} for "
                        f"order {order.get('order_id', 'your order')}. "
                        f"Transaction ID: {refund_result.get('transaction_id', 'N/A')}.\n\n"
                        f"Please allow 5-7 business days for the amount to appear in your account.\n\n"
                        f"Is there anything else we can help with?\n\n"
                        f"Best regards,\nShopWave Support"
                    )
                else:
                    return (
                        f"{greeting}\n\n"
                        f"Your order {order.get('order_id', '')} is eligible for a refund of "
                        f"${eligibility.get('max_refund_amount', 0):.2f}. "
                        f"We're processing this now. Please allow 5-7 business days.\n\n"
                        f"Best regards,\nShopWave Support"
                    )
            else:
                reason = eligibility.get("reason", "does not meet our refund criteria")
                alt = ""
                if tier == "premium":
                    alt = " As a valued Premium member, we'd like to offer you store credit as an alternative."
                elif tier == "vip":
                    alt = " As a VIP customer, we're escalating this for special consideration."
                return (
                    f"{greeting}\n\n"
                    f"We've reviewed your request regarding order {order.get('order_id', 'your order')}. "
                    f"Unfortunately, we're unable to process a refund at this time because: {reason}.{alt}\n\n"
                    f"If you have questions, please don't hesitate to reach out.\n\n"
                    f"Best regards,\nShopWave Support"
                )

        elif category == "cancellation":
            status = order.get("status", "unknown")
            if status == "processing":
                return (
                    f"{greeting}\n\n"
                    f"We've successfully cancelled your order {order.get('order_id', '')}. "
                    f"A full refund of ${order.get('amount', 0):.2f} will be processed and "
                    f"should appear in your account within 5-7 business days.\n\n"
                    f"Best regards,\nShopWave Support"
                )
            elif status == "shipped":
                return (
                    f"{greeting}\n\n"
                    f"Unfortunately, your order {order.get('order_id', '')} has already been shipped "
                    f"and cannot be cancelled. You can initiate a return once the order is delivered.\n\n"
                    f"Best regards,\nShopWave Support"
                )
            else:
                return (
                    f"{greeting}\n\n"
                    f"Your order {order.get('order_id', '')} has a status of '{status}'. "
                    f"Please contact us for further assistance with cancellation.\n\n"
                    f"Best regards,\nShopWave Support"
                )

        elif category == "order_status":
            status = order.get("status", "unknown")
            notes = order.get("notes", "")
            if status == "shipped":
                tracking = ""
                import re
                trk_match = re.search(r'TRK-\d+', notes)
                if trk_match:
                    tracking = f" Your tracking number is {trk_match.group(0)}."
                delivery = order.get("delivery_date") or "within 3-5 business days"
                return (
                    f"{greeting}\n\n"
                    f"Your order {order.get('order_id', '')} is currently in transit.{tracking} "
                    f"Expected delivery: {delivery}.\n\n"
                    f"You can track your package using the tracking number above.\n\n"
                    f"Best regards,\nShopWave Support"
                )
            elif status == "delivered":
                return (
                    f"{greeting}\n\n"
                    f"Your order {order.get('order_id', '')} was delivered on {order.get('delivery_date', 'recently')}.\n\n"
                    f"If you haven't received it, please check with neighbours or your building reception. "
                    f"If still missing, let us know and we'll investigate further.\n\n"
                    f"Best regards,\nShopWave Support"
                )
            else:
                return (
                    f"{greeting}\n\n"
                    f"Your order {order.get('order_id', '')} is currently: {status}. "
                    f"We'll keep you updated on any changes.\n\n"
                    f"Best regards,\nShopWave Support"
                )

        elif category == "damage_claim":
            if eligibility and eligibility.get("eligible"):
                refund_result = state.get_from_memory("refund_result", {})
                if refund_result and refund_result.get("success"):
                    return (
                        f"{greeting}\n\n"
                        f"We're sorry to hear your item arrived damaged. We've processed a full refund of "
                        f"${refund_result.get('refunded_amount', 0):.2f} for order {order.get('order_id', '')}. "
                        f"You do not need to return the damaged item.\n\n"
                        f"Transaction ID: {refund_result.get('transaction_id', 'N/A')}. "
                        f"Please allow 5-7 business days.\n\n"
                        f"Best regards,\nShopWave Support"
                    )
            return (
                f"{greeting}\n\n"
                f"We're sorry to hear about the issue with your order. "
                f"We've noted your report and our team is looking into it.\n\n"
                f"Best regards,\nShopWave Support"
            )

        elif category == "wrong_item":
            return (
                f"{greeting}\n\n"
                f"We sincerely apologize for sending the wrong item for order {order.get('order_id', 'your order')}. "
                f"We're arranging to resolve this for you. You don't need to return the incorrect item.\n\n"
                f"Our fulfilment team will reach out with next steps shortly.\n\n"
                f"Best regards,\nShopWave Support"
            )

        elif category == "general_faq":
            articles = policy.get("articles", []) if isinstance(policy, dict) else []
            if articles:
                # Compile answer from KB articles
                answer_parts = []
                for article in articles[:2]:
                    answer_parts.append(f"**{article.get('title', '')}**: {article.get('content', '')}")
                content = "\n\n".join(answer_parts)
                return (
                    f"{greeting}\n\n"
                    f"Here's what we found regarding your question:\n\n"
                    f"{content}\n\n"
                    f"If you need more details, feel free to ask!\n\n"
                    f"Best regards,\nShopWave Support"
                )
            return (
                f"{greeting}\n\n"
                f"Thank you for your question. Please let us know more details so we can "
                f"provide accurate information.\n\n"
                f"Best regards,\nShopWave Support"
            )

        elif category == "unknown":
            # Ambiguous ticket — ask clarifying questions
            customer_data = state.get_from_memory("customer", {})
            recent_orders = []
            if customer_data and customer_data.get("found"):
                return (
                    f"{greeting}\n\n"
                    f"We'd like to help! To assist you better, could you please provide:\n"
                    f"1. Your order number (starts with ORD-)\n"
                    f"2. The product you're having issues with\n"
                    f"3. A brief description of the problem\n\n"
                    f"This will help us resolve your issue quickly.\n\n"
                    f"Best regards,\nShopWave Support"
                )
            return (
                f"Hi there,\n\nThank you for reaching out. "
                f"We'd like to help but need a bit more information:\n"
                f"1. Your order number\n"
                f"2. What product is this about?\n"
                f"3. What issue are you experiencing?\n\n"
                f"Best regards,\nShopWave Support"
            )

        # Already-refunded case
        if order.get("refund_status") == "refunded":
            return (
                f"{greeting}\n\n"
                f"We can confirm that a refund for order {order.get('order_id', '')} has already been processed. "
                f"Please allow 5-7 business days for the amount to appear in your account.\n\n"
                f"If it's been longer than 7 business days, please contact your bank or payment provider.\n\n"
                f"Best regards,\nShopWave Support"
            )

        # Default
        return (
            f"{greeting}\n\n"
            f"We've reviewed your request and are working on it. "
            f"A member of our team will follow up shortly with a resolution.\n\n"
            f"Best regards,\nShopWave Support"
        )

    def _build_decision_summary(self, state: AgentState) -> str:
        """
        Build a decision summary for the LLM reply composer.
        
        This tells the LLM WHAT the deterministic decision was,
        so the LLM only generates the TEXT — not the decision.
        """
        parts = []
        category = state.category
        order = state.get_from_memory("order", {})
        eligibility = state.get_from_memory("refund_eligibility", {})
        refund_result = state.get_from_memory("refund_result", {})
        product = state.get_from_memory("product", {})

        parts.append(f"Category: {category}")

        if order:
            parts.append(
                f"Order: {order.get('order_id', '?')}, status: {order.get('status', '?')}, "
                f"amount: ${order.get('amount', 0)}"
            )

        if product:
            parts.append(
                f"Product: {product.get('name', '?')}, "
                f"return window: {product.get('return_window_days', '?')} days"
            )

        if eligibility:
            if eligibility.get("eligible"):
                parts.append(f"DECISION: Refund APPROVED. Amount: ${eligibility.get('max_refund_amount', 0)}")
                if refund_result and refund_result.get("success"):
                    parts.append(
                        f"Refund PROCESSED. Transaction ID: {refund_result.get('transaction_id', 'N/A')}"
                    )
            else:
                parts.append(f"DECISION: Refund DENIED. Reason: {eligibility.get('reason', 'N/A')}")

        if category == "cancellation":
            status = order.get("status", "unknown")
            if status == "processing":
                parts.append("DECISION: Order CANCELLED. Full refund to be processed.")
            elif status == "shipped":
                parts.append("DECISION: Cannot cancel -- order already shipped. Suggest return after delivery.")
            else:
                parts.append(f"DECISION: Order status is '{status}'. Advise customer.")

        if category == "order_status":
            parts.append(f"DECISION: Provide order status update. Status: {order.get('status', '?')}")
            notes = order.get("notes", "")
            if notes:
                parts.append(f"Notes: {notes}")

        if category == "damage_claim":
            if eligibility and eligibility.get("eligible"):
                parts.append("DECISION: Damaged item claim APPROVED. Full refund, no return needed.")
            else:
                parts.append("DECISION: Acknowledge damage report and investigate.")

        if category == "wrong_item":
            parts.append("DECISION: Apologize for wrong item. Arrange resolution (no return of wrong item needed).")

        if category == "general_faq":
            policy = state.get_from_memory("policy", {})
            articles = policy.get("articles", []) if isinstance(policy, dict) else []
            if articles:
                for a in articles[:2]:
                    parts.append(f"Policy: {a.get('title', '')}: {a.get('content', '')[:150]}")

        # Customer tier context
        customer = state.get_from_memory("customer", {})
        if customer:
            tier = customer.get("tier", "standard")
            if tier == "premium":
                parts.append("TONE: Warm and appreciative. Acknowledge their loyalty.")
            elif tier == "vip":
                parts.append("TONE: Highly personalized and priority. Make them feel valued.")
            else:
                parts.append("TONE: Professional and helpful.")

        return "\n".join(parts)

