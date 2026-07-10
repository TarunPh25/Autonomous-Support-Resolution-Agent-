# Autonomous Support Resolution Agent

A production-grade, hybrid AI agent that resolves customer support tickets end-to-end using multi-step reasoning, tool usage, policy-aware decisions, and structured audit logging.

## Problem Statement

Customer support teams spend a significant amount of time manually reviewing customer tickets to:
- Understand customer issues
- Retrieve order and customer information
- Verify refund eligibility
- Search company policies
- Decide whether to resolve or escalate
- Draft professional customer responses
- Maintain audit logs for compliance

The Autonomous Support Resolution Agent automates this workflow using LLMs, ReAct reasoning, tool calling, and policy-aware decision making, enabling fast, transparent, and reliable customer support automation.

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Language | Python 3.10+ | Core implementation |
| Data Models | Pydantic v2 | Input/output validation for all tools |
| Async Runtime | asyncio | Concurrent ticket processing |
| LLM (optional) | Groq API (LLaMA 3.3 70B) | Classification, reasoning, reply generation |
| Env Management | python-dotenv | Secure API key storage |
| Architecture | ReAct Loop + Goal-Driven Planner | Multi-step agentic reasoning |

## Project Structure

```text
ksolves/
├── main.py                     # Entry point — CLI, async runner, .env loading
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

## Setup Instructions

### 1. Clone the Repository
```bash
git clone <repo-url>
cd ksolves
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
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
*Note: The system works fully without an API key in deterministic mode. LLM mode is optional.*

## How to Run the Agent

**Basic Run (Deterministic Mode — no API key needed)**
```bash
python main.py
```

**LLM-Assisted Mode (requires GROQ_API_KEY in .env)**
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

### Output Files
After running, the following files are generated:

| File | Description |
|------|-------------|
| `output/audit_logs/TKT-XXX.json` | Individual audit log per ticket |
| `output/combined_audit_log.json` | All 20 tickets in one JSON file |
| `output/agent.log` | Full execution log (human-readable) |

## System Architecture

```text
                         +----------------------+
                         |   Customer Ticket    |
                         |   (tickets.json)     |
                         +----------+-----------+
                                    |
                                    ▼
                           main.py (Entry Point)
                                    |
                                    ▼
                     Decision Engine / Agent Loop
                     (Think → Decide → Act → Observe)
                                    |
            ------------------------------------------------
            |                                              |
            ▼                                              ▼
     Goal Generation                               LLM (Optional)
                                                    Groq LLaMA 3.3
                                                    (llm_client.py)
            |                                              |
            -----------------------+------------------------
                                    |
                                    ▼
                           Tool Registry
                     (Validation + Dispatch)
                                    |
      --------------------------------------------------------------------
      |           |           |          |          |          |           |
      ▼           ▼           ▼          ▼          ▼          ▼           ▼
 Order Tool   Customer   Product Tool   KB Tool   Refund   Communication
 (order.py)   Tool       (product.py)   Search    Tool      Tool
              (customer.py)             (kb.py)   (refund.py) (communication.py)
      |           |           |          |          |          |
      ----------------------------------------------------------
                                    |
                                    ▼
                           JSON Data Sources
      ----------------------------------------------------------------
      |                     |                    |                    |
      ▼                     ▼                    ▼                    ▼
 orders.json      customers.json      products.json     knowledge_base.json
                                               |
                                               ▼
                                      customer_profiles.json
                                    |
                                    ▼
                         Policy-Based Decision Making
                                    |
                                    ▼
                     Confidence Score + Final Resolution
                                    |
                     -----------------------------------
                     |                                 |
                     ▼                                 ▼
              Customer Reply                 Escalation (if needed)
                                    |
                                    ▼
                        Structured Audit Logging
                                    |
                                    ▼
                            audit_log.json
```

The agent follows a ReAct (Reason + Act) pattern with a goal-driven planner:

`Ticket → Classify → Generate Goals → [THINK → DECIDE → ACT → OBSERVE] × N → Resolve/Escalate`

### Core Components
- **Agent Loop** (`agent_loop.py`) — Orchestrates the ReAct reasoning loop
- **Decision Engine** (`decision_engine.py`) — Goal-driven planner that selects the next action
- **Tool Registry** (`tool_registry.py`) — Validates inputs/outputs and wraps tools with retry logic
- **8 Tools** — Order, Customer, Product, Knowledge Base, Refund Eligibility, Issue Refund, Send Reply, Escalate
- **LLM Client** (`llm_client.py`) — Optional Groq API wrapper for enhanced classification, reasoning, and reply composition

### Hybrid Architecture
- Deterministic planner is the source of truth for all decisions
- LLM (optional) enhances classification accuracy, reasoning explanations, and reply quality
- LLM NEVER controls tool selection, escalation, or refund approval
- If LLM fails → silent fallback to deterministic mode

*See `architecture.png` for the full visual diagram.*

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

## Demo Results (20 Tickets)

```text
Total Tickets:   20
Resolved:        14  (70%)
Escalated:        6  (30%)
Failed:           0  (0%)

Total Tool Calls: 99
Avg Confidence:   95.4%
Processing Time:  <1s (deterministic) / ~90s (LLM with rate limits)
```

| Ticket | Category | Status | Confidence | Steps | Key Behavior |
|--------|----------|--------|------------|-------|--------------|
| TKT-001 | refund | Resolved | 100% | 7 | Full refund $129.99, TXN issued |
| TKT-002 | return | Escalated | 100% | 5 | Escalated to tier 2 for supervisor approval |
