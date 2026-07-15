# Failure Modes — Documented Scenarios and Handling

This document describes **5 failure scenarios** the Autonomous Support Resolution Agent encounters and how the system handles each one.

---

## Failure Mode 1: Tool Timeout (Transient Network Failure)

### Scenario
The `get_order` tool times out when trying to fetch order details from the data source. This simulates a real-world API experiencing temporary network latency or overload.

### How It Happens
- Each tool has a built-in failure probability (e.g., `get_order` has a 10% timeout rate)
- When triggered, the tool raises `ToolTimeoutError`

### System Response

```
STEP 1: get_order(ORD-1002) → TIMEOUT
  └─ Retry 1/3: wait 0.05s (with jitter) → retry
     └─ SUCCESS → continue normally
```

**Handling mechanism:**
1. The `retry_with_backoff()` utility catches `ToolTimeoutError`
2. Retries up to 3 times with **exponential backoff** (0.05s → 0.1s → 0.2s)
3. **Jitter** (±50% randomness) prevents thundering herd problems
4. If retry succeeds → processing continues normally
5. If all retries fail → logged as error, `consecutive_failures` incremented

### Evidence from Demo Run
```
20:24:15 | agent.tools.order    | WARNING | get_order(ORD-1002): TIMEOUT
20:24:15 | agent.retry          | WARNING | Retry 1/2 after ToolTimeoutError: 
         Order service timed out for ORD-1002. Waiting 0.036s
20:24:15 | agent.logger         | INFO    | [TKT-002] Step 1: get_order (OK)
```

### Key Design Decision
Transient errors (timeouts, service errors) are retryable. Validation errors (bad input) are NOT retried — they indicate a bug, not a flaky network.

---

## Failure Mode 2: Consecutive Tool Failures → Automatic Escalation

### Scenario
Two or more tools fail back-to-back (even after retries). The agent cannot gather the information it needs to make an autonomous decision.

### How It Happens
- First tool call fails all 3 retry attempts → `consecutive_failures = 1`
- Second tool call also fails all retries → `consecutive_failures = 2`
- Escalation trigger fires

### System Response

```
STEP 3: search_knowledge_base("return policy") → FAIL (3 retries exhausted)
  └─ consecutive_failures = 1

STEP 4: check_refund_eligibility(ORD-XXXX) → FAIL (3 retries exhausted)
  └─ consecutive_failures = 2
  └─ ESCALATION TRIGGERED: "Multiple consecutive tool failures"

STEP 5: escalate(ticket_id, summary, priority="medium")
  └─ Routed to Tier 2 Support
  └─ Summary includes: all actions attempted, what failed, what was gathered
```

**Handling mechanism:**
1. `_check_escalation_triggers()` is called at EVERY step before acting
2. If `state.consecutive_failures >= 2` → immediate escalation
3. Escalation summary includes everything the agent gathered before failure
4. Human agent gets a detailed handoff, not a blank ticket

### Why This Matters
The agent NEVER silently fails. It either resolves the ticket, or hands it to a human with full context of what it tried and what went wrong.

---

## Failure Mode 3: LLM Rate Limiting → Graceful Deterministic Fallback

### Scenario
When running in LLM mode with concurrent tickets, the Groq API returns HTTP 429 (Too Many Requests) because the token-per-minute limit is exceeded.

### How It Happens
- 5 tickets processed simultaneously, each making LLM calls
- Groq free tier limit: 12,000 tokens/minute
- Concurrent calls exceed this limit

### System Response

```
20:24:16 | httpx   | HTTP Request: POST .../chat/completions "HTTP/1.1 429 Too Many Requests"
20:24:16 | agent.llm | WARNING | LLM call failed (RateLimitError): Rate limit 
         reached... Limit 12000, Used 11839, Requested 443.
         Falling back to deterministic.
```

**Handling mechanism:**
1. `LLMClient.call()` catches ALL exceptions — never raises
2. On any failure (timeout, rate limit, network error) → returns `None`
3. The calling code checks for `None` and falls back to deterministic logic:
   - `analyze()` → uses deterministic template thought
   - `classify_ticket_with_llm()` → uses keyword-based classification
   - `_compose_reply()` → uses category-specific reply template
4. The fallback is **silent** — the ticket processes normally without quality loss
5. LLM failure is recorded in the audit trail: `llm_calls[N].success = false`

### Evidence from Demo Run
```
TKT-001 Step 4: [LLM thought failed] → used deterministic thought
TKT-002 Step 3: [LLM thought failed] → used deterministic thought
TKT-004 Step 5: [LLM reply failed]   → used template reply
```
All tickets still resolved/escalated correctly with 0 failures.

### Key Design Decision
The LLM is an **enhancement layer**, not a dependency. The system must function identically without it. This is achieved by:
- Every LLM call has a deterministic fallback computed FIRST
- The `available` property gates all LLM usage
- No control flow or safety decision ever depends on LLM output

---

## Failure Mode 4: Social Engineering Attempt → Fraud Detection + Escalation

### Scenario
A customer claims to be a "premium member" and demands an "instant refund without questions" — but their actual profile shows they are a standard-tier customer.

### How It Happens
- Ticket text contains keywords: "premium member", "instant refund", "without questions"
- Flag `social_engineering` is detected during classification
- Agent fetches actual customer profile and discovers tier mismatch

### System Response

```
STEP 0: Classify → category: refund, flags: [social_engineering, urgency]

STEP 1: get_order(ORD-1002) → Order found: $249.99
STEP 2: get_customer(bob@email.com) → Customer: Bob, tier=STANDARD

STEP 3: _check_escalation_triggers()
  └─ social_engineering flag detected
  └─ Customer claims "premium member" but actual tier = "standard"
  └─ ESCALATION TRIGGERED: "Social engineering detected — customer 
     claims premium/VIP but is standard tier"

STEP 3: escalate(ticket_id, summary, priority="high")
  └─ Routed to Trust & Safety
```

**Handling mechanism:**
1. `FLAG_SIGNALS["social_engineering"]` keywords detected during classification
2. After customer profile is fetched, `_check_escalation_triggers()` cross-references:
   - Does ticket text claim premium/VIP?
   - Does actual profile show standard tier?
3. If tier mismatch → escalate to **Trust & Safety** with high priority
4. Agent NEVER processes a refund for a social engineering attempt

### Evidence from Demo Run (TKT-018)
```
TKT-018 | refund | [ESC] escalated | 78.7% | 3 steps | social_engineering, urgency
  → Routed to Trust & Safety [ESC-xxxxxxxx]
```

---

## Failure Mode 5: Refund Safety Gate — Blocked Without Eligibility Check

### Scenario
Due to a logic error or adversarial prompt, the agent attempts to call `issue_refund` without first calling `check_refund_eligibility`.

### How This Is Prevented

The system has a **two-layer safety gate**:

**Layer 1: Agent Loop Pre-Action Check**
```python
# In agent_loop.py, before every tool execution:
if tool_name == "issue_refund" and not state.refund_eligibility_checked:
    # BLOCK: Redirect to eligibility check first
    tool_name = "check_refund_eligibility"
    reason = "Safety constraint: Must check eligibility before issuing refund."
```

**Layer 2: Tool-Level Duplicate Prevention**
```python
# In refund.py, inside issue_refund():
if order_id in _refunds_issued:
    return {"success": False, "reason": "Refund already issued for this order"}
```

**Layer 3: Decision Engine — Dynamic Goal Addition**
- `issue_refund` goal is only added to the goal list AFTER `check_refund_eligibility` returns `eligible=True` AND the amount is ≤$200
- For amounts >$200, the refund goal is NEVER added — escalation fires instead

### System Response (if gate triggers)

```
Agent tries: issue_refund(ORD-1001, $129.99)
  └─ SAFETY CHECK: refund_eligibility_checked = False
  └─ BLOCKED → redirected to check_refund_eligibility(ORD-1001)
  └─ After eligibility confirmed → issue_refund proceeds normally
```

### Key Design Decision
Financial actions require explicit validation. The agent cannot skip steps, even if the goal planner unexpectedly orders them incorrectly. This provides defense-in-depth: even a bug in goal ordering cannot cause an unauthorized refund.

---

## Summary Table

| # | Failure Mode | Detection | Response | Outcome |
|---|-------------|-----------|----------|---------|
| 1 | Tool timeout | `ToolTimeoutError` caught | Retry 3× with backoff + jitter | Usually recovers |
| 2 | Consecutive failures | `consecutive_failures ≥ 2` | Auto-escalate to human | Clean handoff |
| 3 | LLM rate limit | `RateLimitError` caught | Silent fallback to deterministic | Zero disruption |
| 4 | Social engineering | Keyword flags + tier mismatch | Escalate to Trust & Safety | Fraud prevented |
| 5 | Refund without check | Pre-action safety gate | Block + redirect to eligibility | Unauthorized refund prevented |
