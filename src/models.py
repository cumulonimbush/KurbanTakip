"""
models.py — V2.1 Domain models for the Kurban Tracking Application.

V2.1 changes
-------------
* ``share_fraction`` (int, 1–7) added to ``AnimalShare`` and staging models.
* ``price_per_share`` / ``weight_per_share`` now compute fraction-weighted
  values: ``(total / sum_of_fractions) * this_fraction``.
* New convenience ``total_fractions`` property on ``AnimalRecord``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple

_PRICE_PLACES = Decimal("0.01")
_WEIGHT_PLACES = Decimal("0.001")


# ═══════════════════════════════════════════════════════════════════════════
# Persisted domain models (frozen)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Shareholder:
    """A person identified by their phone number (E.164)."""
    phone: str
    name: str


@dataclass(frozen=True)
class AnimalShare:
    """Junction record linking one shareholder to one animal."""
    animal_id: int
    phone: str
    shareholder_name: str
    is_paid: bool
    share_fraction: int = 1       # NEW in V2.1

    # -- fraction-aware computed helpers ──────────────────────────────

    def price_for(self, total_price: Decimal, total_fractions: int) -> Decimal:
        """This shareholder's cost based on their fraction."""
        if total_fractions == 0:
            return Decimal("0.00")
        return (total_price / total_fractions * self.share_fraction).quantize(
            _PRICE_PLACES, rounding=ROUND_HALF_UP
        )

    def weight_for(self, total_weight: Decimal, total_fractions: int) -> Decimal:
        """This shareholder's weight based on their fraction."""
        if total_fractions == 0:
            return Decimal("0.000")
        return (total_weight / total_fractions * self.share_fraction).quantize(
            _WEIGHT_PLACES, rounding=ROUND_HALF_UP
        )


@dataclass(frozen=True)
class AnimalRecord:
    """A fully-hydrated animal with metadata and all associated shares."""
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
        """Sum of all share fractions (denominator for per-share calc)."""
        return sum(s.share_fraction for s in self.shares)

    @property
    def price_per_unit_fraction(self) -> Decimal:
        """Price for 1 unit of fraction."""
        tf = self.total_fractions
        if tf == 0:
            return Decimal("0.00")
        return (self.total_price / tf).quantize(_PRICE_PLACES, rounding=ROUND_HALF_UP)

    @property
    def weight_per_unit_fraction(self) -> Decimal:
        """Weight for 1 unit of fraction."""
        tf = self.total_fractions
        if tf == 0:
            return Decimal("0.000")
        return (self.total_weight / tf).quantize(_WEIGHT_PLACES, rounding=ROUND_HALF_UP)


# ═══════════════════════════════════════════════════════════════════════════
# Staging models (mutable)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StagedShareholderEntry:
    """One shareholder row inside the staging form."""
    phone: str
    name: str
    is_paid: bool
    share_fraction: int = 1       # NEW in V2.1


@dataclass
class StagedAnimal:
    """An animal waiting in the staging area before DB commit."""
    slaughter_date: date
    total_price: Decimal
    total_weight: Decimal
    shareholders: List[StagedShareholderEntry] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Pagination helper
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PaginatedResult:
    """Wraps a page of ``AnimalRecord`` objects with pagination metadata."""
    records: Tuple[AnimalRecord, ...]
    page: int
    per_page: int
    total_records: int

    @property
    def total_pages(self) -> int:
        if self.total_records == 0:
            return 1
        return (self.total_records + self.per_page - 1) // self.per_page
