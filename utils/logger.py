"""
Structured audit logging — creates detailed JSON logs for every ticket resolution.
Captures thoughts, tool calls, observations, policy references, and confidence reasoning.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional
import logging

logger = logging.getLogger("agent.logger")


class AuditLogger:
    """
    Per-ticket audit logger that builds a structured JSON record
    of the agent's entire reasoning chain.
    """

    def __init__(self, ticket_id: str, output_dir: str = "output/audit_logs"):
        self.ticket_id = ticket_id
        self.output_dir = output_dir
        self.steps: list[dict] = []
        self.errors: list[dict] = []
        self.start_time = time.time()
        self.metadata: dict = {}

        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

    def add_step(
        self,
        step_num: int,
        thought: str,
        action: str,
        action_input: dict,
        observation: Any,
        success: bool = True,
        reason: str = "",
        latency_ms: float = 0,
        retries: int = 0
    ):
        """Record a single reasoning step."""
        step_record = {
            "step": step_num,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "thought": thought,
            "action": action,
            "action_input": _sanitize(action_input),
            "observation": _sanitize(observation),
            "success": success,
            "reason": reason,
            "latency_ms": round(latency_ms, 2),
            "retries": retries
        }
        self.steps.append(step_record)
        logger.info(
            f"[{self.ticket_id}] Step {step_num}: {action} "
            f"({'OK' if success else 'FAIL'}) -- {thought[:80]}..."
        )

    def add_error(self, error_type: str, message: str, context: str = ""):
        """Record an error encountered during processing."""
        self.errors.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": error_type,
            "message": message,
            "context": context
        })

    def set_metadata(self, key: str, value: Any):
        """Store metadata about the ticket processing."""
        self.metadata[key] = value

    def finalize(
        self,
        status: str,
        confidence: float,
        confidence_reason: str = "",
        policy_references: list[str] = None,
        resolution_message: str = "",
        category: str = "",
        customer_tier: str = "",
        flags: list[str] = None,
    ) -> dict:
        """
        Finalize the audit log and write to disk.
        Returns the complete audit record.
        """
        duration_ms = (time.time() - self.start_time) * 1000

        audit_record = {
            "ticket_id": self.ticket_id,
            "category": category,
            "customer_tier": customer_tier,
            "processing_summary": {
                "final_status": status,
                "confidence": round(confidence, 3),
                "confidence_reason": confidence_reason,
                "total_steps": len(self.steps),
                "total_tool_calls": len([s for s in self.steps if s["action"] not in ("analyze", "final_decision")]),
                "total_duration_ms": round(duration_ms, 2),
                "errors_encountered": len(self.errors),
                "flags": flags or [],
            },
            "policy_references": policy_references or [],
            "resolution_message": resolution_message,
            "steps": self.steps,
            "errors": self.errors,
            "metadata": self.metadata,
            "generated_at": datetime.now(timezone.utc).isoformat()
        }

        # Write to file
        filepath = os.path.join(self.output_dir, f"{self.ticket_id}.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(audit_record, f, indent=2, ensure_ascii=False, default=str)
            logger.info(f"[{self.ticket_id}] Audit log written to {filepath}")
        except Exception as e:
            logger.error(f"[{self.ticket_id}] Failed to write audit log: {e}")

        return audit_record


def _sanitize(obj: Any) -> Any:
    """Sanitize objects for JSON serialization."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(item) for item in obj]
    return str(obj)


def print_summary(audit_logs: list[dict]):
    """Print a formatted summary of all processed tickets."""
    resolved = [l for l in audit_logs if l["processing_summary"]["final_status"] == "resolved"]
    escalated = [l for l in audit_logs if l["processing_summary"]["final_status"] == "escalated"]
    needs_info = [l for l in audit_logs if l["processing_summary"]["final_status"] == "needs_info"]
    failed = [l for l in audit_logs if l["processing_summary"]["final_status"] == "failed"]

    total_time = sum(l["processing_summary"]["total_duration_ms"] for l in audit_logs)
    total_steps = sum(l["processing_summary"]["total_steps"] for l in audit_logs)
    total_tools = sum(l["processing_summary"]["total_tool_calls"] for l in audit_logs)
    avg_confidence = (
        sum(l["processing_summary"]["confidence"] for l in audit_logs) / len(audit_logs)
        if audit_logs else 0
    )

    print("\n" + "=" * 80)
    print("  AUTONOMOUS SUPPORT RESOLUTION AGENT -- EXECUTION SUMMARY")
    print("=" * 80)
    print(f"\n  Total Tickets Processed:  {len(audit_logs)}")
    print(f"  [OK]   Resolved:          {len(resolved)}")
    print(f"  [ESC]  Escalated:         {len(escalated)}")
    print(f"  [?]    Needs Info:        {len(needs_info)}")
    print(f"  [FAIL] Failed:            {len(failed)}")
    print(f"\n  Total Reasoning Steps:    {total_steps}")
    print(f"  Total Tool Calls:         {total_tools}")
    print(f"  Avg Confidence:           {avg_confidence:.1%}")
    print(f"  Total Processing Time:    {total_time:.0f}ms")
    print(f"\n  Audit logs saved to:      output/audit_logs/")
    print("=" * 80)

    # Per-ticket details
    print(f"\n{'Ticket':<10} {'Category':<18} {'Status':<12} {'Confidence':<12} {'Steps':<8} {'Tools':<8} {'Flags'}")
    print("-" * 100)
    for log in audit_logs:
        ps = log["processing_summary"]
        status_icon = {
            "resolved": "[OK] ", "escalated": "[ESC]", "needs_info": "[?]  ", "failed": "[!!] "
        }.get(ps["final_status"], "     ")
        flags_str = ", ".join(ps.get("flags", [])) or "-"
        print(
            f"{log['ticket_id']:<10} {log.get('category', '?'):<18} "
            f"{status_icon} {ps['final_status']:<9} {ps['confidence']:<12.1%} "
            f"{ps['total_steps']:<8} {ps['total_tool_calls']:<8} {flags_str}"
        )
    print()
