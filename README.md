# Autonomous Support Resolution Agent

> A production-grade, hybrid AI agent that resolves customer support tickets end-to-end using multi-step reasoning, tool usage, policy-aware decisions, and structured audit logging. Includes a beautiful FastAPI-powered web dashboard for real-time visualization and control.

---

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Language | Python 3.10+ | Core implementation |
| Data Models | Pydantic v2 | Input/output validation for all tools |
| Async Runtime | asyncio | Concurrent ticket processing |
| LLM (optional) | Groq API (LLaMA 3.3 70B) | Classification, reasoning, reply generation |
| Env Management | python-dotenv | Secure API key storage |
| Architecture | ReAct Loop + Goal-Driven Planner | Multi-step agentic reasoning |

---

## Project Structure

```
Internship-main/
├── main.py                     # Entry point — CLI, async runner, .env loading
├── server.py                   # FastAPI Web Server for Dashboard
├── static/                     # Web dashboard assets (HTML, JS, CSS)
├── agent/
│   ├── agent_loop.py           # ReAct reasoning loop (Think → Decide → Act → Observe)
│   ├── decision_engine.py      # Goal-driven planner + hybrid LLM integration
│   └── tool_registry.py        # Tool dispatch with Pydantic validation + retry
├── tools/
│   ├── order.py                # get_order(order_id)
│   ├── customer.py             # get_customer(email)
│   ├── product.py              # get_product(product_id)
│   ├── kb.py                   # search_knowledge_base(query)
│   ├── refund.py               # check_refund_eligibility + issue_refund
│   └── communication.py        # send_reply + escalate
├── models/
│   ├── state.py                # AgentState, ReasoningStep, InformationGoal
│   └── ticket.py               # Ticket + TicketCategory models
├── utils/
│   ├── retry.py                # Exponential backoff with jitter
│   ├── validator.py            # Pydantic schemas for all 8 tools
│   ├── logger.py               # Structured JSON audit logger
│   └── llm_client.py           # Groq API wrapper with graceful fallback
├── data/
│   ├── knowledge_base.json     # 12 policy articles
│   └── customer_profiles.json  # 10 customer profiles
├── tickets.json                # 20 support tickets (input)
├── customers.json              # 15 orders
├── products.json               # 8 products
├── .env                        # API keys (gitignored)
├── .env.example                # Template for .env
├── .gitignore                  # Protects .env
├── requirements.txt            # Dependencies
├── failure_modes.md            # Documented failure scenarios
├── architecture.png            # System architecture diagram
├── audit_log.json              # Demo run output (20 tickets)
└── README.md                   # This file
```

---

## Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/TarunPh25/Autonomous-Support-Resolution-Agent-.git
cd Autonomous-Support-Resolution-Agent-
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt fastapi uvicorn
```

This installs:
- `pydantic>=2.0` — data validation
- `groq>=0.4.0` — LLM API client (optional)
- `python-dotenv>=1.0.0` — environment variable loading

### 3. Configure Environment (Optional — for LLM mode)

```bash
# Copy the example env file
cp .env.example .env

# Edit .env and add your Groq API key
# Get a free key at: https://console.groq.com/keys
GROQ_API_KEY=gsk_your_actual_key_here
AGENT_MODE=llm
```

> **Note:** The system works fully without an API key in deterministic mode. LLM mode is optional.

---

## How to Run the Agent

### Running the Web Dashboard (Recommended)

To launch the FastAPI web server and access the interactive dashboard:

```bash
python server.py
```
Then, open your web browser and navigate to: **http://localhost:8000**

You can configure the Groq LLM API directly from the settings panel in the web interface.

---

### Basic Run (CLI Deterministic Mode — no API key needed)

```bash
python main.py
```

### LLM-Assisted Mode (requires GROQ_API_KEY in .env)

```bash
python main.py --mode llm
```

### All CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--tickets N` | All (20) | Process first N tickets only |
| `--concurrency N` | 5 | Max parallel ticket processing |
| `--mode` | deterministic | `deterministic` or `llm` |
| `--verbose` | Off | Enable debug-level logging |

### Examples

```bash
# Process 5 tickets with debug logging
python main.py --tickets 5 --verbose

# Process all tickets with LLM, max 3 concurrent
python main.py --mode llm --concurrency 3

# Quick deterministic test
python main.py --tickets 3 --concurrency 1
```

---

## Output Files

After running, the following files are generated:

| File | Description |
|------|-------------|
| `output/audit_logs/TKT-XXX.json` | Individual audit log per ticket |
| `output/combined_audit_log.json` | All 20 tickets in one JSON file |
| `output/agent.log` | Full execution log (human-readable) |

---

## System Architecture

The agent follows a **ReAct (Reason + Act)** pattern with a **goal-driven planner**:

```
Ticket → Classify → Generate Goals → [THINK → DECIDE → ACT → OBSERVE] × N → Resolve/Escalate
```

### Core Components

1. **Agent Loop** (`agent_loop.py`) — Orchestrates the ReAct reasoning loop
2. **Decision Engine** (`decision_engine.py`) — Goal-driven planner that selects the next action
3. **Tool Registry** (`tool_registry.py`) — Validates inputs/outputs and wraps tools with retry logic
4. **8 Tools** — Order, Customer, Product, Knowledge Base, Refund Eligibility, Issue Refund, Send Reply, Escalate
5. **LLM Client** (`llm_client.py`) — Optional Groq API wrapper for enhanced classification, reasoning, and reply composition

### Hybrid Architecture

- **Deterministic planner** is the source of truth for all decisions
- **LLM** (optional) enhances classification accuracy, reasoning explanations, and reply quality
- LLM **NEVER** controls tool selection, escalation, or refund approval
- If LLM fails → silent fallback to deterministic mode

See `architecture.png` for the full visual diagram.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Multi-step Reasoning** | 3-7 tool calls per ticket in a ReAct loop |
| **Goal-Driven Planning** | Dynamic goals based on ticket signals, not hardcoded flows |
| **8 Callable Tools** | Real data access with failure simulation |
| **Policy-Aware Decisions** | 12 business policies from knowledge base |
| **Customer Tier Intelligence** | Standard/Premium/VIP affect tone and flexibility |
| **7 Escalation Triggers** | Safety rules that route to human teams |
| **Confidence Scoring** | 4-factor weighted formula (data, tools, policy, clarity) |
| **Safety Controls** | Refund gate, $200 limit, social engineering detection |
| **Structured Audit Logs** | Full JSON trace of every reasoning step |
| **Async Concurrency** | Process multiple tickets in parallel |
| **Failure Recovery** | Exponential backoff retry with jitter |
| **Hybrid LLM** | Optional Groq/LLaMA 3 for enhanced quality |

---

## Demo Results (20 Tickets)

```
Total Tickets:   20
Resolved:        14  (70%)
Escalated:        6  (30%)
Failed:           0  (0%)

Total Tool Calls: 99
Avg Confidence:   95.4%
Processing Time:  <1s (deterministic) / ~90s (LLM with rate limits)
```

| Ticket | Category | Status | Confidence | Steps | Key Behavior |
|--------|----------|--------|------------|-------|-------------|
| TKT-001 | refund | Resolved | 100% | 7 | Full refund $129.99, TXN issued |
| TKT-002 | return | Escalated | 100% | 5 | >$200 → supervisor required |
| TKT-003 | damage_claim | Escalated | 100% | 4 | Replacement → fulfilment team |
| TKT-004 | wrong_item | Resolved | 100% | 6 | Wrong size, threatening language |
| TKT-005 | return | Resolved | 100% | 6 | VIP exception honored |
| TKT-006 | cancellation | Resolved | 82.5% | 3 | Processing → cancelled |
| TKT-007 | return | Resolved | 100% | 7 | Within 60-day window |
| TKT-008 | damage_claim | Resolved | 100% | 7 | Full refund, no return needed |
| TKT-009 | refund | Resolved | 100% | 6 | Already refunded confirmed |
| TKT-010 | order_status | Resolved | 82.5% | 4 | Tracking info shared |
| TKT-011 | wrong_item | Escalated | 100% | 5 | Wrong colour + >$200 |
| TKT-012 | cancellation | Resolved | 100% | 7 | Cancelled + refund processed |
| TKT-013 | return | Resolved | 100% | 6 | Expired return denied |
| TKT-014 | return | Escalated | 100% | 5 | >$200 supervisor approval |
| TKT-015 | damage_claim | Escalated | 100% | 4 | Replacement → fulfilment |
| TKT-016 | refund | Resolved | 82.5% | 3 | Unknown customer handled |
| TKT-017 | refund | Resolved | 100% | 5 | Non-existent order denied |
| TKT-018 | refund | Escalated | 78.7% | 3 | Social engineering → Trust & Safety |
| TKT-019 | general_faq | Resolved | 100% | 3 | Policy answer from KB |
| TKT-020 | damage_claim | Resolved | 82.5% | 3 | Damage acknowledged |
