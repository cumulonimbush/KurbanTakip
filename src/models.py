"""
models.py — V2.0 Domain models for the Kurban Tracking Application.

Changes from V1
----------------
* ``tc_no`` replaced by ``phone`` (E.164 formatted string).
* ``total_price`` / ``total_weight`` added as ``Decimal`` fields on animals.
* Per-share price/weight computed dynamically — never stored.
* All money is in **kuruş** (1/100 TRY) and weight in **grams** internally;
  the ``Decimal`` ↔ INTEGER conversion lives in the repository layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple

# Number of decimal places for display (TRY → kuruş = 2, kg → gram = 3)
_PRICE_PLACES = Decimal("0.01")
_WEIGHT_PLACES = Decimal("0.001")


# ═══════════════════════════════════════════════════════════════════════════
# Persisted domain models (frozen — treat as read-only after hydration)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Shareholder:
    """A person identified by their phone number (E.164)."""

    phone: str          # e.g. "+905551234567"
    name: str


@dataclass(frozen=True)
class AnimalShare:
    """Junction record linking one shareholder to one animal."""

    animal_id: int
    phone: str
    shareholder_name: str
    is_paid: bool


@dataclass(frozen=True)
class AnimalRecord:
    """A fully-hydrated animal with metadata and all associated shares."""

    animal_id: int
    slaughter_date: date
    total_price: Decimal          # TRY (e.g. Decimal("15000.00"))
    total_weight: Decimal         # kg  (e.g. Decimal("250.500"))
    shares: Tuple[AnimalShare, ...] = field(default_factory=tuple)

    # -- computed properties ──────────────────────────────────────────

    @property
    def share_count(self) -> int:
        return len(self.shares)

    @property
    def price_per_share(self) -> Decimal:
        """Price per shareholder in TRY, rounded to 2 decimal places."""
        if self.share_count == 0:
            return Decimal("0.00")
        return (self.total_price / self.share_count).quantize(
            _PRICE_PLACES, rounding=ROUND_HALF_UP
        )

    @property
    def weight_per_share(self) -> Decimal:
        """Weight per shareholder in kg, rounded to 3 decimal places."""
        if self.share_count == 0:
            return Decimal("0.000")
        return (self.total_weight / self.share_count).quantize(
            _WEIGHT_PLACES, rounding=ROUND_HALF_UP
        )


# ═══════════════════════════════════════════════════════════════════════════
# Staging-only models (mutable — GUI populates these before commit)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StagedShareholderEntry:
    """One shareholder row inside the staging form."""

    phone: str          # raw user input; normalised to E.164 by controller
    name: str
    is_paid: bool


@dataclass
class StagedAnimal:
    """An animal waiting in the staging area before DB commit."""

    slaughter_date: date
    total_price: Decimal          # TRY
    total_weight: Decimal         # kg
    shareholders: List[StagedShareholderEntry] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Pagination helper
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PaginatedResult:
    """Wraps a page of ``AnimalRecord`` objects with pagination metadata."""

    records: Tuple[AnimalRecord, ...]
    page: int               # 1-based current page
    per_page: int
    total_records: int

    @property
    def total_pages(self) -> int:
        if self.total_records == 0:
            return 1
        return (self.total_records + self.per_page - 1) // self.per_page
