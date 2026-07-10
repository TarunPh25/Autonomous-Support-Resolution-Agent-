"""
Autonomous Support Resolution Agent — Main Entry Point

Processes customer support tickets end-to-end using a multi-step ReAct
reasoning loop with tool integration, policy-aware decisions, and full audit logging.

Usage:
    python main.py                     # Process all 20 tickets
    python main.py --tickets 5         # Process first 5 tickets
    python main.py --concurrency 3     # Limit to 3 concurrent tickets
    python main.py --verbose           # Enable debug logging

Architecture:
    Tickets → Agent Loop (ReAct) → Decision Engine (Goal Planner) → Tools → Audit Logs
"""

import asyncio
import json
import os
import sys
import time
import logging
import argparse
from typing import List

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env file (GROQ_API_KEY, AGENT_MODE, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv not installed — env vars must be set manually

from agent.agent_loop import resolve_ticket
from agent.tool_registry import create_default_registry
from agent.decision_engine import DecisionEngine
from utils.logger import print_summary
from utils.llm_client import init_llm_client


def setup_logging(verbose: bool = False):
    """Configure logging for the agent system."""
    level = logging.DEBUG if verbose else logging.INFO
    
    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S"
    )

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)

    # File handler
    os.makedirs("output", exist_ok=True)
    file_handler = logging.FileHandler("output/agent.log", mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)


def load_tickets(filepath: str = "tickets.json") -> List[dict]:
    """Load tickets from the JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tickets = json.load(f)
        return tickets
    except FileNotFoundError:
        # Fallback to orders.json (same content)
        with open("orders.json", "r", encoding="utf-8") as f:
            return json.load(f)


async def process_ticket(
    ticket: dict,
    registry,
    engine: DecisionEngine,
    semaphore: asyncio.Semaphore,
    output_dir: str
) -> dict:
    """Process a single ticket with semaphore-controlled concurrency."""
    async with semaphore:
        try:
            result = await resolve_ticket(ticket, registry, engine, output_dir)
            return result
        except Exception as e:
            logging.getLogger("agent.main").error(
                f"[{ticket.get('ticket_id', '?')}] Unhandled error: {e}",
                exc_info=True
            )
            return {
                "ticket_id": ticket.get("ticket_id", "UNKNOWN"),
                "category": "error",
                "processing_summary": {
                    "final_status": "failed",
                    "confidence": 0.0,
                    "confidence_reason": f"Unhandled error: {e}",
                    "total_steps": 0,
                    "total_tool_calls": 0,
                    "total_duration_ms": 0,
                    "errors_encountered": 1,
                    "flags": [],
                },
                "policy_references": [],
                "resolution_message": "",
                "steps": [],
                "errors": [{"error_type": "unhandled", "message": str(e)}],
            }


async def main(args):
    """Main async entry point."""
    logger = logging.getLogger("agent.main")

    # Setup
    output_dir = "output/audit_logs"
    os.makedirs(output_dir, exist_ok=True)

    # Load tickets
    tickets = load_tickets()
    if args.tickets:
        tickets = tickets[:args.tickets]

    logger.info(f"Loaded {len(tickets)} tickets for processing")

    # Determine mode: CLI flag > env var > default
    mode = args.mode or os.environ.get("AGENT_MODE", "deterministic")
    use_llm = (mode == "llm")

    # If LLM mode, initialize the client
    if use_llm:
        llm_client = init_llm_client()
        if llm_client.available:
            logger.info("LLM mode: ACTIVE (Groq API)")
        else:
            logger.warning(
                "LLM mode requested but GROQ_API_KEY not set or groq package not installed. "
                "Falling back to deterministic mode. "
                "Set GROQ_API_KEY env var or run: pip install groq"
            )
            use_llm = False
            mode = "deterministic"

    # Create tool registry and decision engine
    registry = create_default_registry()
    engine = DecisionEngine(use_llm=use_llm)

    mode_label = "LLM-ASSISTED (Groq)" if engine.use_llm else "DETERMINISTIC"

    logger.info(f"Tool registry created with {len(registry.get_tool_names())} tools: {registry.get_tool_names()}")
    logger.info(f"Concurrency limit: {args.concurrency}")
    logger.info(f"Mode: {mode_label}")

    # ── Process tickets concurrently ──
    semaphore = asyncio.Semaphore(args.concurrency)
    start_time = time.time()

    print("\n" + "=" * 80)
    print("  [AGENT] AUTONOMOUS SUPPORT RESOLUTION AGENT")
    print(f"  Mode: {mode_label}")
    print(f"  Tickets: {len(tickets)} | Concurrency: {args.concurrency}")
    print("=" * 80 + "\n")

    # Create tasks for concurrent execution
    tasks = [
        process_ticket(ticket, registry, engine, semaphore, output_dir)
        for ticket in tickets
    ]

    # Execute all tickets concurrently
    audit_logs = await asyncio.gather(*tasks)

    elapsed = time.time() - start_time
    logger.info(f"All {len(tickets)} tickets processed in {elapsed:.2f}s")

    # ── Save combined audit log ──
    combined_path = os.path.join("output", "combined_audit_log.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(audit_logs, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Combined audit log saved to {combined_path}")

    # ── Print summary ──
    print_summary(audit_logs)

    print(f"\n  [TIME] Total wall-clock time: {elapsed:.2f}s")
    print(f"  [DIR]  Individual logs: {output_dir}/")
    print(f"  [DIR]  Combined log: {combined_path}")
    print(f"  [DIR]  Agent log: output/agent.log\n")

    return audit_logs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Autonomous Support Resolution Agent"
    )
    parser.add_argument(
        "--tickets", type=int, default=None,
        help="Number of tickets to process (default: all)"
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="Max concurrent ticket processing (default: 5)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--mode", type=str, default=None,
        choices=["deterministic", "llm"],
        help="Agent mode: 'deterministic' (default) or 'llm' (Groq API)"
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    asyncio.run(main(args))
