"""
app/state_machine.py

Explicit, independently-testable state machine for a Transaction. This is
what makes "DENIED can never reach payment" a structural guarantee rather
than a hope — every status change in the orchestrator goes through
`transition()`, which raises on anything not in ALLOWED_TRANSITIONS.
"""

from __future__ import annotations


class InvalidTransitionError(Exception):
    def __init__(self, current: str, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current} -> {target}")


CREATED = "CREATED"
POLICY_CHECKED = "POLICY_CHECKED"
APPROVED = "APPROVED"
ESCALATED = "ESCALATED"
DENIED = "DENIED"
AWAITING_PAYMENT = "AWAITING_PAYMENT"
PAYMENT_PENDING = "PAYMENT_PENDING"
PAYMENT_CREATED = "PAYMENT_CREATED"
PAYMENT_FAILED = "PAYMENT_FAILED"
COMPLETED = "COMPLETED"
CANCELLED = "CANCELLED"

ALL_STATES = {
    CREATED,
    POLICY_CHECKED,
    APPROVED,
    ESCALATED,
    DENIED,
    AWAITING_PAYMENT,
    PAYMENT_PENDING,
    PAYMENT_CREATED,
    PAYMENT_FAILED,
    COMPLETED,
    CANCELLED,
}

# The authoritative transition table. Anything not listed here is forbidden —
# in particular there is no entry that lets DENIED (or CANCELLED) reach any
# payment or completion state.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    CREATED: {POLICY_CHECKED, DENIED, CANCELLED},
    POLICY_CHECKED: {APPROVED, ESCALATED, DENIED},
    APPROVED: {AWAITING_PAYMENT, CANCELLED},
    ESCALATED: {APPROVED, DENIED, CANCELLED},  # human approves -> APPROVED, rejects -> DENIED
    DENIED: set(),  # terminal, no exit — this is the safety guarantee
    AWAITING_PAYMENT: {PAYMENT_PENDING, CANCELLED},
    PAYMENT_PENDING: {PAYMENT_CREATED, PAYMENT_FAILED},
    PAYMENT_CREATED: {COMPLETED, PAYMENT_FAILED},
    PAYMENT_FAILED: {PAYMENT_PENDING, CANCELLED},  # allow a fresh payment attempt, or give up
    COMPLETED: set(),  # terminal
    CANCELLED: set(),  # terminal
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def transition(current: str, target: str) -> str:
    """Validate and return the new status, or raise InvalidTransitionError.
    Callers must persist the returned status — this function has no side effects."""
    if target not in ALL_STATES:
        raise InvalidTransitionError(current, target)
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)
    return target


def is_terminal(state: str) -> bool:
    return len(ALLOWED_TRANSITIONS.get(state, set())) == 0
