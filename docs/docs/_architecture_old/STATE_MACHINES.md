# SOS State Machines

Status: Draft v0.1
Owner: Claude Code
Last Updated: 2026-01-10

## Purpose
Define explicit state machines for complex workflows in SOS. State machines prevent edge case bugs and make system behavior predictable and auditable.

---

## 1. Task Lifecycle

### State Diagram

```
                                    ┌─────────────┐
                                    │   pending   │
                                    └──────┬──────┘
                                           │ claim
                                           ▼
                              ┌────────────────────────┐
                              │        claimed         │
                              └───────────┬────────────┘
                                          │ start
                                          ▼
                         ┌────────────────────────────────┐
                         │         in_progress            │
                         └───────┬───────────────┬────────┘
                                 │               │
                          abandon│               │submit
                                 ▼               ▼
                    ┌────────────────┐    ┌─────────────┐
                    │   abandoned    │    │   review    │
                    └───────┬────────┘    └──────┬──────┘
                            │                    │
                     reopen │         ┌──────────┴──────────┐
                            │         │                     │
                            ▼         ▼                     ▼
                    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
                    │   pending   │ │  completed  │ │  rejected   │
                    └─────────────┘ └─────────────┘ └──────┬──────┘
                                                          │
                                                   reopen │
                                                          ▼
                                                   ┌─────────────┐
                                                   │   pending   │
                                                   └─────────────┘
```

### States

| State | Description | Allowed Transitions |
|-------|-------------|---------------------|
| `pending` | Task created, awaiting claim | `claim` → claimed |
| `claimed` | Agent has claimed task | `start` → in_progress, `unclaim` → pending |
| `in_progress` | Work is actively happening | `submit` → review, `abandon` → abandoned |
| `review` | Work submitted for review | `approve` → completed, `reject` → rejected |
| `completed` | Task successfully finished | Terminal |
| `rejected` | Work did not meet criteria | `reopen` → pending |
| `abandoned` | Agent gave up on task | `reopen` → pending |

### Transitions

```python
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

class TaskState(Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ABANDONED = "abandoned"

@dataclass
class TaskTransition:
    from_state: TaskState
    to_state: TaskState
    action: str
    actor: str
    timestamp: datetime
    reason: Optional[str] = None

# Valid transitions
TASK_TRANSITIONS = {
    TaskState.PENDING: {
        "claim": TaskState.CLAIMED,
    },
    TaskState.CLAIMED: {
        "start": TaskState.IN_PROGRESS,
        "unclaim": TaskState.PENDING,
    },
    TaskState.IN_PROGRESS: {
        "submit": TaskState.REVIEW,
        "abandon": TaskState.ABANDONED,
    },
    TaskState.REVIEW: {
        "approve": TaskState.COMPLETED,
        "reject": TaskState.REJECTED,
    },
    TaskState.REJECTED: {
        "reopen": TaskState.PENDING,
    },
    TaskState.ABANDONED: {
        "reopen": TaskState.PENDING,
    },
    TaskState.COMPLETED: {},  # Terminal state
}

class TaskStateMachine:
    def __init__(self, task_id: str, initial_state: TaskState = TaskState.PENDING):
        self.task_id = task_id
        self.state = initial_state
        self.history: list[TaskTransition] = []

    def can_transition(self, action: str) -> bool:
        """Check if action is valid from current state."""
        return action in TASK_TRANSITIONS.get(self.state, {})

    def transition(self, action: str, actor: str, reason: str = None) -> TaskState:
        """Execute state transition."""
        if not self.can_transition(action):
            raise InvalidTransitionError(
                f"Cannot {action} from state {self.state.value}"
            )

        new_state = TASK_TRANSITIONS[self.state][action]

        self.history.append(TaskTransition(
            from_state=self.state,
            to_state=new_state,
            action=action,
            actor=actor,
            timestamp=datetime.now(),
            reason=reason
        ))

        self.state = new_state
        return new_state

    def get_available_actions(self) -> list[str]:
        """Get valid actions from current state."""
        return list(TASK_TRANSITIONS.get(self.state, {}).keys())
```

### Constraints

```python
# Time-based constraints
TASK_CONSTRAINTS = {
    TaskState.CLAIMED: {
        "max_duration_hours": 24,  # Must start within 24h or auto-unclaim
    },
    TaskState.IN_PROGRESS: {
        "max_duration_hours": 168,  # 1 week max
        "checkpoint_interval_hours": 24,  # Must checkpoint daily
    },
    TaskState.REVIEW: {
        "max_duration_hours": 48,  # Review must complete within 48h
    },
}

async def enforce_task_constraints(task: Task) -> None:
    """Enforce time-based task constraints."""
    constraint = TASK_CONSTRAINTS.get(task.state)
    if not constraint:
        return

    hours_in_state = (datetime.now() - task.state_entered_at).total_seconds() / 3600

    if hours_in_state > constraint.get("max_duration_hours", float("inf")):
        # Auto-transition based on state
        if task.state == TaskState.CLAIMED:
            await task.transition("unclaim", actor="system", reason="Claim timeout")
        elif task.state == TaskState.IN_PROGRESS:
            await task.transition("abandon", actor="system", reason="Work timeout")
        elif task.state == TaskState.REVIEW:
            # Escalate to Mumega
            await notify_escalation(task, reason="Review timeout")
```

---

## 2. Agent Onboarding Flow

### State Diagram

```
┌─────────────┐
│  anonymous  │
└──────┬──────┘
       │ identify
       ▼
┌─────────────┐
│ identified  │
└──────┬──────┘
       │ verify
       ▼
┌─────────────┐
│  verified   │
└──────┬──────┘
       │ assign_squad
       ▼
┌────────────────┐
│ squad_assigned │
└───────┬────────┘
        │ activate
        ▼
  ┌──────────┐
  │  active  │
  └────┬─────┘
       │
       ├─── suspend ──► suspended ──► reactivate ──► active
       │
       └─── terminate ──► terminated (terminal)
```

### States

| State | Description | Capabilities |
|-------|-------------|--------------|
| `anonymous` | Unknown entity | None |
| `identified` | Has provided identity claim | View public info |
| `verified` | Identity verified by River | Limited tool access |
| `squad_assigned` | Assigned to squad/guild | Squad-scoped access |
| `active` | Fully operational | Full capabilities per role |
| `suspended` | Temporarily disabled | Read-only |
| `terminated` | Permanently removed | None |

### Implementation

```python
class AgentState(Enum):
    ANONYMOUS = "anonymous"
    IDENTIFIED = "identified"
    VERIFIED = "verified"
    SQUAD_ASSIGNED = "squad_assigned"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"

AGENT_TRANSITIONS = {
    AgentState.ANONYMOUS: {
        "identify": AgentState.IDENTIFIED,
    },
    AgentState.IDENTIFIED: {
        "verify": AgentState.VERIFIED,
        "reject": AgentState.ANONYMOUS,
    },
    AgentState.VERIFIED: {
        "assign_squad": AgentState.SQUAD_ASSIGNED,
    },
    AgentState.SQUAD_ASSIGNED: {
        "activate": AgentState.ACTIVE,
    },
    AgentState.ACTIVE: {
        "suspend": AgentState.SUSPENDED,
        "terminate": AgentState.TERMINATED,
    },
    AgentState.SUSPENDED: {
        "reactivate": AgentState.ACTIVE,
        "terminate": AgentState.TERMINATED,
    },
    AgentState.TERMINATED: {},  # Terminal
}

@dataclass
class AgentOnboarding:
    agent_id: str
    state: AgentState
    identity_claim: Optional[dict] = None
    verification_result: Optional[dict] = None
    squad_id: Optional[str] = None
    activated_at: Optional[datetime] = None

class OnboardingStateMachine:
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.state = AgentState.ANONYMOUS
        self.data = AgentOnboarding(agent_id=agent_id, state=self.state)

    async def identify(self, identity_claim: dict) -> AgentState:
        """Agent provides identity claim."""
        self._require_state(AgentState.ANONYMOUS)

        self.data.identity_claim = identity_claim
        return self._transition("identify")

    async def verify(self, verifier: str) -> AgentState:
        """River verifies identity."""
        self._require_state(AgentState.IDENTIFIED)

        # River performs verification
        result = await river_verify_identity(self.data.identity_claim)
        if not result.success:
            return self._transition("reject")

        self.data.verification_result = result
        return self._transition("verify")

    async def assign_squad(self, squad_id: str) -> AgentState:
        """Assign agent to squad."""
        self._require_state(AgentState.VERIFIED)

        self.data.squad_id = squad_id
        return self._transition("assign_squad")

    async def activate(self) -> AgentState:
        """Activate agent for full operation."""
        self._require_state(AgentState.SQUAD_ASSIGNED)

        self.data.activated_at = datetime.now()
        return self._transition("activate")

    def _require_state(self, required: AgentState) -> None:
        if self.state != required:
            raise InvalidStateError(f"Expected {required.value}, got {self.state.value}")

    def _transition(self, action: str) -> AgentState:
        new_state = AGENT_TRANSITIONS[self.state][action]
        self.state = new_state
        self.data.state = new_state
        return new_state
```

---

## 3. Economy Transaction Flow

### State Diagram

```
┌──────────────┐
│   proposed   │
└──────┬───────┘
       │ validate
       ▼
┌──────────────┐
│  validated   │
└──────┬───────┘
       │
       ├── requires_witness=true ──► pending_witness ──► witnessed ──► committed
       │
       └── requires_witness=false ──► committed
                                           │
                                           ▼
                                      ┌─────────┐
                                      │ settled │
                                      └─────────┘
```

### States

| State | Description |
|-------|-------------|
| `proposed` | Transaction created |
| `validated` | Passed validation rules |
| `pending_witness` | Awaiting witness approval |
| `witnessed` | Witness has approved |
| `committed` | Written to ledger |
| `settled` | Finalized (e.g., on-chain) |
| `rejected` | Validation or witness failed |

### Implementation

```python
class TxState(Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    PENDING_WITNESS = "pending_witness"
    WITNESSED = "witnessed"
    COMMITTED = "committed"
    SETTLED = "settled"
    REJECTED = "rejected"

@dataclass
class Transaction:
    tx_id: str
    from_agent: str
    to_agent: str
    amount: int
    currency: str
    tx_type: str  # payout, slash, transfer
    state: TxState
    requires_witness: bool
    witness_id: Optional[str] = None
    reason: str = ""

class TransactionStateMachine:
    def __init__(self, tx: Transaction):
        self.tx = tx

    async def validate(self) -> TxState:
        """Validate transaction against rules."""
        self._require_state(TxState.PROPOSED)

        # Check balance
        balance = await get_balance(self.tx.from_agent, self.tx.currency)
        if balance < self.tx.amount:
            return self._transition_to(TxState.REJECTED, "Insufficient balance")

        # Check daily limits
        daily_total = await get_daily_total(self.tx.from_agent, self.tx.tx_type)
        limit = get_daily_limit(self.tx.tx_type)
        if daily_total + self.tx.amount > limit:
            return self._transition_to(TxState.REJECTED, "Daily limit exceeded")

        return self._transition_to(TxState.VALIDATED)

    async def request_witness(self) -> TxState:
        """Request witness approval if required."""
        self._require_state(TxState.VALIDATED)

        if not self.tx.requires_witness:
            return self._transition_to(TxState.COMMITTED)

        # Notify available witnesses
        await notify_witnesses(self.tx)
        return self._transition_to(TxState.PENDING_WITNESS)

    async def witness_approve(self, witness_id: str) -> TxState:
        """Witness approves transaction."""
        self._require_state(TxState.PENDING_WITNESS)

        self.tx.witness_id = witness_id
        return self._transition_to(TxState.WITNESSED)

    async def commit(self) -> TxState:
        """Commit transaction to ledger."""
        allowed_states = [TxState.VALIDATED, TxState.WITNESSED]
        if self.tx.state not in allowed_states:
            raise InvalidTransitionError(f"Cannot commit from {self.tx.state.value}")

        # Write to ledger
        await ledger_write(self.tx)
        return self._transition_to(TxState.COMMITTED)

    async def settle(self) -> TxState:
        """Finalize transaction (e.g., on-chain settlement)."""
        self._require_state(TxState.COMMITTED)

        # If on-chain settlement required
        if self.tx.currency in ON_CHAIN_CURRENCIES:
            await blockchain_settle(self.tx)

        return self._transition_to(TxState.SETTLED)

    def _require_state(self, required: TxState) -> None:
        if self.tx.state != required:
            raise InvalidStateError(f"Expected {required.value}, got {self.tx.state.value}")

    def _transition_to(self, new_state: TxState, reason: str = "") -> TxState:
        self.tx.state = new_state
        if reason:
            self.tx.reason = reason
        return new_state
```

---

## 4. Capability Lifecycle

### State Diagram

```
┌───────────┐
│  issued   │
└─────┬─────┘
      │
      ├── use ──► active ──► (returns to issued after use)
      │
      ├── expire ──► expired
      │
      └── revoke ──► revoked
```

### Implementation

```python
class CapabilityState(Enum):
    ISSUED = "issued"
    ACTIVE = "active"  # Currently being used
    EXPIRED = "expired"
    REVOKED = "revoked"

@dataclass
class CapabilityUsage:
    capability_id: str
    state: CapabilityState
    uses_remaining: Optional[int]
    expires_at: datetime
    last_used_at: Optional[datetime] = None

class CapabilityStateMachine:
    def __init__(self, cap: CapabilityUsage):
        self.cap = cap

    def use(self) -> bool:
        """Attempt to use capability."""
        # Check expiration
        if datetime.now() > self.cap.expires_at:
            self.cap.state = CapabilityState.EXPIRED
            return False

        # Check if already revoked/expired
        if self.cap.state in [CapabilityState.EXPIRED, CapabilityState.REVOKED]:
            return False

        # Check uses remaining
        if self.cap.uses_remaining is not None:
            if self.cap.uses_remaining <= 0:
                self.cap.state = CapabilityState.EXPIRED
                return False
            self.cap.uses_remaining -= 1

        self.cap.state = CapabilityState.ACTIVE
        self.cap.last_used_at = datetime.now()

        # Return to issued after use
        self.cap.state = CapabilityState.ISSUED
        return True

    def revoke(self, reason: str) -> None:
        """Revoke capability."""
        self.cap.state = CapabilityState.REVOKED
        log.info(f"Capability revoked", cap_id=self.cap.capability_id, reason=reason)
```

---

## 5. Testing State Machines

```python
import pytest

class TestTaskStateMachine:
    def test_happy_path(self):
        sm = TaskStateMachine("task_001")
        assert sm.state == TaskState.PENDING

        sm.transition("claim", actor="kasra")
        assert sm.state == TaskState.CLAIMED

        sm.transition("start", actor="kasra")
        assert sm.state == TaskState.IN_PROGRESS

        sm.transition("submit", actor="kasra")
        assert sm.state == TaskState.REVIEW

        sm.transition("approve", actor="river")
        assert sm.state == TaskState.COMPLETED

    def test_invalid_transition(self):
        sm = TaskStateMachine("task_001")

        with pytest.raises(InvalidTransitionError):
            sm.transition("approve", actor="river")  # Can't approve pending task

    def test_rejection_reopen(self):
        sm = TaskStateMachine("task_001")
        sm.transition("claim", actor="kasra")
        sm.transition("start", actor="kasra")
        sm.transition("submit", actor="kasra")
        sm.transition("reject", actor="river", reason="Missing tests")
        assert sm.state == TaskState.REJECTED

        sm.transition("reopen", actor="mumega")
        assert sm.state == TaskState.PENDING

    def test_history_tracking(self):
        sm = TaskStateMachine("task_001")
        sm.transition("claim", actor="kasra")
        sm.transition("start", actor="kasra")

        assert len(sm.history) == 2
        assert sm.history[0].action == "claim"
        assert sm.history[1].action == "start"
```

---

## Implementation Checklist

- [ ] Task state machine with all transitions
- [ ] Task constraint enforcement (timeouts)
- [ ] Agent onboarding state machine
- [ ] River verification integration
- [ ] Transaction state machine
- [ ] Witness approval flow
- [ ] Capability lifecycle tracking
- [ ] State machine event logging
- [ ] Unit tests for all state machines
