"""
models.py — V2.2 Domain models for the Kurban Tracking Application.

V2.2 changes
-------------
* ``is_paid`` replaced by ``paid_amount: Decimal`` on ``AnimalShare``.
* Payment status derived dynamically: Unpaid / Partial / Paid.
* ``StagedShareholderEntry`` takes ``paid_amount: Decimal`` instead of ``is_paid``.
* New ``DashboardStats`` dataclass for the analytics pane.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List, Tuple

_PRICE_PLACES = Decimal("0.01")
_WEIGHT_PLACES = Decimal("0.001")


class PaymentStatus(Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"


# ═══════════════════════════════════════════════════════════════════════════
# Persisted domain models (frozen)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Shareholder:
    phone: str
    name: str


@dataclass(frozen=True)
class AnimalShare:
    animal_id: int
    phone: str
    shareholder_name: str
    paid_amount: Decimal          # V2.2: replaces is_paid
    share_fraction: int = 1

    def price_for(self, total_price: Decimal, total_fractions: int) -> Decimal:
        if total_fractions == 0:
            return Decimal("0.00")
        return (total_price / total_fractions * self.share_fraction).quantize(
            _PRICE_PLACES, rounding=ROUND_HALF_UP
        )

    def weight_for(self, total_weight: Decimal, total_fractions: int) -> Decimal:
        if total_fractions == 0:
            return Decimal("0.000")
        return (total_weight / total_fractions * self.share_fraction).quantize(
            _WEIGHT_PLACES, rounding=ROUND_HALF_UP
        )

    def payment_status(self, total_price: Decimal, total_fractions: int) -> PaymentStatus:
        """Derive status from paid_amount vs expected share price."""
        expected = self.price_for(total_price, total_fractions)
        if self.paid_amount <= 0:
            return PaymentStatus.UNPAID
        if self.paid_amount >= expected:
            return PaymentStatus.PAID
        return PaymentStatus.PARTIAL


@dataclass(frozen=True)
class AnimalRecord:
    animal_id: int
    slaughter_date: date
    total_price: Decimal
    total_weight: Decimal
    shares: Tuple[AnimalShare, ...] = field(default_factory=tuple)

    @property
    def share_count(self) -> int:
        return len(self.shares)

    @property
    def total_fractions(self) -> int:
        return sum(s.share_fraction for s in self.shares)

    @property
    def price_per_unit_fraction(self) -> Decimal:
        tf = self.total_fractions
        if tf == 0:
            return Decimal("0.00")
        return (self.total_price / tf).quantize(_PRICE_PLACES, rounding=ROUND_HALF_UP)

    @property
    def weight_per_unit_fraction(self) -> Decimal:
        tf = self.total_fractions
        if tf == 0:
            return Decimal("0.000")
        return (self.total_weight / tf).quantize(_WEIGHT_PLACES, rounding=ROUND_HALF_UP)

    @property
    def total_paid(self) -> Decimal:
        """Sum of all paid amounts across shares."""
        return sum((s.paid_amount for s in self.shares), Decimal("0.00"))


# ═══════════════════════════════════════════════════════════════════════════
# Staging models (mutable)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StagedShareholderEntry:
    phone: str
    name: str
    paid_amount: Decimal = Decimal("0.00")   # V2.2: replaces is_paid
    share_fraction: int = 1


@dataclass
class StagedAnimal:
    slaughter_date: date
    total_price: Decimal
    total_weight: Decimal
    shareholders: List[StagedShareholderEntry] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Dashboard aggregates
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DashboardStats:
    """Aggregated metrics for the Dashboard tab, computed via SQL."""
    total_animals: int
    total_share_capacity: int        # 7 × total_animals
    sold_shares: int                 # count of animal_shares rows
    total_fractions_sold: int        # sum of share_fraction across all
    expected_revenue_kurus: int      # sum of all animals' total_price_kurus
    collected_amount_kurus: int      # sum of all paid_amount_kurus
    unsold_shares: int = 0           # capacity - sold

    @property
    def expected_revenue(self) -> Decimal:
        return Decimal(self.expected_revenue_kurus) / Decimal("100")

    @property
    def collected_amount(self) -> Decimal:
        return Decimal(self.collected_amount_kurus) / Decimal("100")

    @property
    def outstanding_balance(self) -> Decimal:
        return self.expected_revenue - self.collected_amount


# ═══════════════════════════════════════════════════════════════════════════
# Pagination helper
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PaginatedResult:
    records: Tuple[AnimalRecord, ...]
    page: int
    per_page: int
    total_records: int

    @property
    def total_pages(self) -> int:
        if self.total_records == 0:
            return 1
        return (self.total_records + self.per_page - 1) // self.per_page
