"""
Centralized Payment Status Enums
================================
TD-010: Standardize payment status across MindEconomy, WorkSettlement, BountyBoard, Treasury.

This module provides consistent status values for all payment-related operations.
"""

from enum import Enum


class PaymentStatus(Enum):
    """Status of a payment transaction."""
    PENDING = "pending"           # Payment initiated, awaiting processing
    SUCCESS = "success"           # Payment completed successfully
    FAILED = "failed"             # Payment failed (error during execution)
    SIMULATED = "simulated"       # Payment simulated (no real transaction)
    SKIPPED = "skipped"           # Payment skipped (e.g., disabled)
    TREASURY_UNAVAILABLE = "treasury_unavailable"  # Treasury not configured
    BLOCKED = "blocked"           # Payment blocked by safety check

    def __str__(self) -> str:
        return self.value


class TransactionStatus(Enum):
    """Status of an on-chain transaction verification."""
    CONFIRMED = "confirmed"       # Transaction confirmed on-chain
    PENDING = "pending"           # Transaction sent, awaiting confirmation
    FAILED = "failed"             # Transaction failed on-chain
    NOT_FOUND = "not_found"       # Transaction not found
    ERROR = "error"               # Error during verification

    def __str__(self) -> str:
        return self.value


class BountyStatus(Enum):
    """Status of a bounty in the BountyBoard.

    Note: Uses uppercase values to match existing bounty JSON data format.
    """
    OPEN = "OPEN"                 # Bounty available for claiming
    CLAIMED = "CLAIMED"           # Bounty claimed by a worker
    SUBMITTED = "SUBMITTED"       # Work submitted, awaiting review
    APPROVED = "APPROVED"         # Work approved, payment pending
    PAID = "PAID"                 # Payment completed
    EXPIRED = "EXPIRED"           # Bounty expired (unclaimed or unsubmitted)
    REFUND_PENDING = "REFUND_PENDING"  # Awaiting refund to creator
    REFUNDED = "REFUNDED"         # Refund completed
    CANCELED = "CANCELED"         # Bounty canceled by creator

    def __str__(self) -> str:
        return self.value


class WorkStatus(Enum):
    """Status of a work unit in the Work system."""
    OPEN = "open"                 # Work available
    ASSIGNED = "assigned"         # Work assigned to worker
    IN_PROGRESS = "in_progress"   # Work being done
    SUBMITTED = "submitted"       # Work submitted for review
    IN_REVIEW = "in_review"       # Under review
    APPROVED = "approved"         # Work approved
    REJECTED = "rejected"         # Work rejected
    PAID = "paid"                 # Payment completed
    DISPUTED = "disputed"         # Under dispute
    CANCELED = "canceled"         # Work canceled

    def __str__(self) -> str:
        return self.value
