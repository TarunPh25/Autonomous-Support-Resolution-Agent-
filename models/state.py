"""
Agent state management — maintains context across reasoning steps.
Implements the goal-driven planner's state model.
"""

from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum


class TicketStatus(str, Enum):
    """Final resolution status of a ticket."""
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    NEEDS_INFO = "needs_info"
    FAILED = "failed"


class ReasoningStep(BaseModel):
    """A single step in the agent's reasoning chain."""
    step: int
    timestamp: str
    thought: str
    action: str
    action_input: dict = Field(default_factory=dict)
    observation: Any = None
    success: bool = True
    reason: str = ""  # Why this action was chosen


class ToolResult(BaseModel):
    """Result from a tool execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    latency_ms: float = 0
    retries: int = 0


class InformationGoal(BaseModel):
    """
    A goal in the agent's goal-driven planner.
    Goals are dynamically generated from ticket analysis and updated 
    after each observation. The engine selects the highest-priority
    unsatisfied goal at each step.
    """
    goal_id: str
    description: str
    priority: float = 0.5  # 0.0 - 1.0, higher = more important
    satisfied: bool = False
    required_tool: Optional[str] = None
    required_params: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    result_key: str = ""  # Key to store result in memory


class AgentState(BaseModel):
    """
    Full state of the agent while processing a single ticket.
    Each ticket gets its own independent state — no shared mutable state.
    """
    ticket_id: str
    ticket_data: dict = Field(default_factory=dict)  # Raw ticket
    status: TicketStatus = TicketStatus.IN_PROGRESS
    steps: list[ReasoningStep] = Field(default_factory=list)
    memory: dict = Field(default_factory=dict)  # Key-value store for intermediate results
    goals: list[InformationGoal] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)
    tool_call_log: list[dict] = Field(default_factory=list)  # Full log of tool calls
    consecutive_failures: int = 0
    total_failures: int = 0
    total_tool_calls: int = 0
    confidence: float = 0.5
    confidence_reason: str = ""
    policy_references: list[str] = Field(default_factory=list)
    customer_tier: Optional[str] = None
    category: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    max_steps: int = 15
    current_step: int = 0
    start_time: float = 0
    resolution_message: str = ""

    # Safety tracking
    refund_eligibility_checked: bool = False
    refund_eligible: bool = False
    refund_max_amount: float = 0.0

    class Config:
        arbitrary_types_allowed = True

    def record_tool_call(self, tool_name: str, success: bool):
        """Track tool call outcomes for confidence calculation."""
        self.tools_called.append(tool_name)
        self.total_tool_calls += 1
        if not success:
            self.consecutive_failures += 1
            self.total_failures += 1
        else:
            self.consecutive_failures = 0

    def get_next_unsatisfied_goal(self) -> Optional[InformationGoal]:
        """Get the highest-priority unsatisfied goal whose dependencies are met."""
        satisfied_ids = {g.goal_id for g in self.goals if g.satisfied}
        candidates = [
            g for g in self.goals
            if not g.satisfied and all(d in satisfied_ids for d in g.depends_on)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda g: g.priority)

    def satisfy_goal(self, goal_id: str):
        """Mark a goal as satisfied."""
        for g in self.goals:
            if g.goal_id == goal_id:
                g.satisfied = True
                break

    def add_to_memory(self, key: str, value: Any):
        """Store intermediate result in memory."""
        self.memory[key] = value

    def get_from_memory(self, key: str, default: Any = None) -> Any:
        """Retrieve from memory."""
        return self.memory.get(key, default)

    @property
    def all_goals_satisfied(self) -> bool:
        return all(g.satisfied for g in self.goals)

    @property
    def should_escalate(self) -> bool:
        """Check if automated escalation triggers are met."""
        if self.consecutive_failures >= 2:
            return True
        if self.confidence < 0.4:
            return True
        return False
