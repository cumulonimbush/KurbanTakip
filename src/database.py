"""
database.py — V2.1 SQLite schema, connection management, and Repository.

V2.1 changes
-------------
* ``animal_shares`` gains ``share_fraction INTEGER NOT NULL DEFAULT 1``.
* Safe ``ALTER TABLE`` migration in ``initialise_database`` for existing V2.0 DBs.
* ``_hydrate_animals`` reads ``share_fraction`` from rows.
* ``_JOIN_SQL`` includes ``ash.share_fraction``.
* New methods: ``add_share_to_animal``, ``remove_share_from_animal`` for
  full CRUD in the Edit Dialog.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Generator, List, Optional

from models import AnimalRecord, AnimalShare, PaginatedResult, StagedAnimal

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Nuitka-safe base path
# ---------------------------------------------------------------------------


def _get_app_dir() -> Path:
    # Windows'ta güvenli veri yazma alanı: Belgelerim / KurbanTakip
    data_dir = Path.home() / "Documents" / "KurbanTakip"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

APP_DIR = _get_app_dir()
DB_FILE = APP_DIR / "kurban.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS animals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slaughter_date      DATE    NOT NULL,
    total_price_kurus   INTEGER NOT NULL DEFAULT 0,
    total_weight_grams  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS shareholders (
    phone   TEXT PRIMARY KEY,
    name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS animal_shares (
    animal_id       INTEGER NOT NULL,
    phone           TEXT    NOT NULL,
    is_paid         BOOLEAN NOT NULL DEFAULT 0,
    share_fraction  INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (animal_id, phone),
    FOREIGN KEY (animal_id) REFERENCES animals(id)         ON DELETE CASCADE,
    FOREIGN KEY (phone)     REFERENCES shareholders(phone) ON DELETE CASCADE
);
"""

# ---------------------------------------------------------------------------
# V2.0 → V2.1 migration: add share_fraction if missing
# ---------------------------------------------------------------------------

_MIGRATION_SQL = """\
ALTER TABLE animal_shares ADD COLUMN share_fraction INTEGER NOT NULL DEFAULT 1;
"""

# ---------------------------------------------------------------------------
# Decimal ↔ INTEGER helpers
# ---------------------------------------------------------------------------

_KURUS_FACTOR = Decimal("100")
_GRAM_FACTOR = Decimal("1000")


def _try_to_kurus(value: Decimal) -> int:
    return int((value * _KURUS_FACTOR).to_integral_value())


def _kurus_to_try(value: int) -> Decimal:
    return Decimal(value) / _KURUS_FACTOR


def _kg_to_grams(value: Decimal) -> int:
    return int((value * _GRAM_FACTOR).to_integral_value())


def _grams_to_kg(value: int) -> Decimal:
    return Decimal(value) / _GRAM_FACTOR


# ---------------------------------------------------------------------------
# Connection context manager
# ---------------------------------------------------------------------------

@contextmanager
def _get_connection(
    db_path: Path = DB_FILE,
) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema bootstrap + migration
# ---------------------------------------------------------------------------

def initialise_database(db_path: Path = DB_FILE) -> None:
    """Create tables and run safe migrations for V2.0 → V2.1."""
    with _get_connection(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        # Safe migration: add share_fraction column if it doesn't exist yet
        try:
            cols = [
                row["name"]
                for row in conn.execute("PRAGMA table_info(animal_shares)").fetchall()
            ]
            if "share_fraction" not in cols:
                conn.execute(_MIGRATION_SQL)
                conn.commit()
                logger.info("Migration: added share_fraction column to animal_shares")
        except sqlite3.OperationalError:
            # Column already exists or table doesn't exist yet (handled by schema above)
            pass

    logger.info("Database initialised at %s", db_path)


# ═══════════════════════════════════════════════════════════════════════════
# Repository
# ═══════════════════════════════════════════════════════════════════════════

class KurbanRepository:
    """Data-access layer — every SQL statement lives here and only here."""

    def __init__(self, db_path: Path = DB_FILE) -> None:
        self._db_path = db_path

    # ── WRITE ────────────────────────────────────────────────────────────

    def commit_staged_animals(self, staged: List[StagedAnimal]) -> List[int]:
        """Persist a full batch inside one transaction."""
        inserted_ids: List[int] = []
        with _get_connection(self._db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN")
                for animal in staged:
                    cursor.execute(
                        "INSERT INTO animals "
                        "(slaughter_date, total_price_kurus, total_weight_grams) "
                        "VALUES (?, ?, ?)",
                        (
                            animal.slaughter_date.isoformat(),
                            _try_to_kurus(animal.total_price),
                            _kg_to_grams(animal.total_weight),
                        ),
                    )
                    animal_id = cursor.lastrowid
                    assert animal_id is not None
                    inserted_ids.append(animal_id)

                    for sh in animal.shareholders:
                        cursor.execute(
                            "INSERT INTO shareholders (phone, name) VALUES (?, ?) "
                            "ON CONFLICT(phone) DO UPDATE SET name=excluded.name",
                            (sh.phone, sh.name),
                        )
                        cursor.execute(
                            "INSERT INTO animal_shares "
                            "(animal_id, phone, is_paid, share_fraction) "
                            "VALUES (?, ?, ?, ?)",
                            (animal_id, sh.phone, int(sh.is_paid), sh.share_fraction),
                        )
                conn.commit()
                logger.info(
                    "Committed %d animals (IDs: %s)", len(inserted_ids), inserted_ids
                )
            except Exception:
                conn.rollback()
                logger.exception("Batch commit failed — rolled back")
                raise
        return inserted_ids

    def update_payment_status(
        self, animal_id: int, phone: str, is_paid: bool
    ) -> None:
        with _get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE animal_shares SET is_paid = ? "
                "WHERE animal_id = ? AND phone = ?",
                (int(is_paid), animal_id, phone),
            )
            conn.commit()
        logger.info(
            "Payment updated: animal=%d phone=%s is_paid=%s",
            animal_id, phone, is_paid,
        )

    def update_animal(
        self,
        animal_id: int,
        slaughter_date: date,
        total_price: Decimal,
        total_weight: Decimal,
    ) -> None:
        with _get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE animals SET slaughter_date=?, "
                "total_price_kurus=?, total_weight_grams=? WHERE id=?",
                (
                    slaughter_date.isoformat(),
                    _try_to_kurus(total_price),
                    _kg_to_grams(total_weight),
                    animal_id,
                ),
            )
            conn.commit()
        logger.info("Animal %d updated", animal_id)

    def delete_animal(self, animal_id: int) -> None:
        """Delete an animal — CASCADE removes its shares automatically."""
        with _get_connection(self._db_path) as conn:
            conn.execute("DELETE FROM animals WHERE id = ?", (animal_id,))
            conn.commit()
        logger.info("Animal %d deleted (cascaded shares)", animal_id)

    # ── V2.1 — Share-level CRUD ─────────────────────────────────────────

    def add_share_to_animal(
        self,
        animal_id: int,
        phone: str,
        name: str,
        is_paid: bool,
        share_fraction: int = 1,
    ) -> None:
        """Add a new shareholder to an existing animal."""
        with _get_connection(self._db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN")
                cursor.execute(
                    "INSERT INTO shareholders (phone, name) VALUES (?, ?) "
                    "ON CONFLICT(phone) DO UPDATE SET name=excluded.name",
                    (phone, name),
                )
                cursor.execute(
                    "INSERT INTO animal_shares "
                    "(animal_id, phone, is_paid, share_fraction) "
                    "VALUES (?, ?, ?, ?)",
                    (animal_id, phone, int(is_paid), share_fraction),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        logger.info(
            "Share added: animal=%d phone=%s fraction=%d",
            animal_id, phone, share_fraction,
        )

    def remove_share_from_animal(self, animal_id: int, phone: str) -> None:
        """Remove a single shareholder from an animal."""
        with _get_connection(self._db_path) as conn:
            conn.execute(
                "DELETE FROM animal_shares WHERE animal_id = ? AND phone = ?",
                (animal_id, phone),
            )
            conn.commit()
        logger.info("Share removed: animal=%d phone=%s", animal_id, phone)

    # ── READ ─────────────────────────────────────────────────────────────

    _JOIN_SQL = (
        "SELECT a.id, a.slaughter_date, "
        "       a.total_price_kurus, a.total_weight_grams, "
        "       ash.phone, s.name, ash.is_paid, ash.share_fraction "
        "FROM animals a "
        "JOIN animal_shares ash ON ash.animal_id = a.id "
        "JOIN shareholders  s   ON s.phone = ash.phone "
    )

    def _hydrate_animals(self, rows: list) -> List[AnimalRecord]:
        """Group flat joined rows into ``AnimalRecord`` objects."""
        if not rows:
            return []

        records: List[AnimalRecord] = []
        current_id: Optional[int] = None
        current_date: Optional[date] = None
        current_price: int = 0
        current_weight: int = 0
        current_shares: list[AnimalShare] = []

        for row in rows:
            aid = row["id"]
            if aid != current_id:
                if current_id is not None and current_date is not None:
                    records.append(
                        AnimalRecord(
                            animal_id=current_id,
                            slaughter_date=current_date,
                            total_price=_kurus_to_try(current_price),
                            total_weight=_grams_to_kg(current_weight),
                            shares=tuple(current_shares),
                        )
                    )
                current_id = aid
                current_date = date.fromisoformat(row["slaughter_date"])
                current_price = row["total_price_kurus"]
                current_weight = row["total_weight_grams"]
                current_shares = []
            current_shares.append(
                AnimalShare(
                    animal_id=aid,
                    phone=row["phone"],
                    shareholder_name=row["name"],
                    is_paid=bool(row["is_paid"]),
                    share_fraction=row["share_fraction"],
                )
            )

        if current_id is not None and current_date is not None:
            records.append(
                AnimalRecord(
                    animal_id=current_id,
                    slaughter_date=current_date,
                    total_price=_kurus_to_try(current_price),
                    total_weight=_grams_to_kg(current_weight),
                    shares=tuple(current_shares),
                )
            )
        return records

    def search_by_animal_id(self, animal_id: int) -> Optional[AnimalRecord]:
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(
                self._JOIN_SQL + "WHERE a.id = ? ORDER BY ash.rowid",
                (animal_id,),
            ).fetchall()
        result = self._hydrate_animals(rows)
        return result[0] if result else None

    def search_by_phone_or_name(self, query: str) -> List[AnimalRecord]:
        like_query = f"%{query}%"
        with _get_connection(self._db_path) as conn:
            animal_ids = [
                row["animal_id"]
                for row in conn.execute(
                    "SELECT DISTINCT ash.animal_id "
                    "FROM animal_shares ash "
                    "JOIN shareholders s ON s.phone = ash.phone "
                    "WHERE ash.phone LIKE ? OR s.name LIKE ?",
                    (like_query, like_query),
                ).fetchall()
            ]
            if not animal_ids:
                return []
            placeholders = ",".join("?" for _ in animal_ids)
            rows = conn.execute(
                self._JOIN_SQL
                + f"WHERE a.id IN ({placeholders}) ORDER BY a.id, ash.rowid",
                animal_ids,
            ).fetchall()
        return self._hydrate_animals(rows)

    # ── PAGINATED READ ───────────────────────────────────────────────────

    def count_all_animals(self) -> int:
        with _get_connection(self._db_path) as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM animals").fetchone()
        return row["cnt"] if row else 0

    def count_search_results(self, query: str) -> int:
        like_query = f"%{query}%"
        with _get_connection(self._db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT ash.animal_id) AS cnt "
                "FROM animal_shares ash "
                "JOIN shareholders s ON s.phone = ash.phone "
                "WHERE ash.phone LIKE ? OR s.name LIKE ?",
                (like_query, like_query),
            ).fetchone()
        return row["cnt"] if row else 0

    def get_animals_paginated(
        self, page: int = 1, per_page: int = 50
    ) -> PaginatedResult:
        total = self.count_all_animals()
        offset = (page - 1) * per_page
        with _get_connection(self._db_path) as conn:
            page_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM animals ORDER BY id DESC LIMIT ? OFFSET ?",
                    (per_page, offset),
                ).fetchall()
            ]
            if not page_ids:
                return PaginatedResult(
                    records=(), page=page, per_page=per_page, total_records=total
                )
            placeholders = ",".join("?" for _ in page_ids)
            rows = conn.execute(
                self._JOIN_SQL
                + f"WHERE a.id IN ({placeholders}) ORDER BY a.id DESC, ash.rowid",
                page_ids,
            ).fetchall()
        records = self._hydrate_animals(rows)
        return PaginatedResult(
            records=tuple(records), page=page, per_page=per_page, total_records=total
        )

    def search_paginated(
        self, query: str, page: int = 1, per_page: int = 50
    ) -> PaginatedResult:
        like_query = f"%{query}%"
        total = self.count_search_results(query)
        offset = (page - 1) * per_page
        with _get_connection(self._db_path) as conn:
            page_ids = [
                row["animal_id"]
                for row in conn.execute(
                    "SELECT DISTINCT ash.animal_id "
                    "FROM animal_shares ash "
                    "JOIN shareholders s ON s.phone = ash.phone "
                    "WHERE ash.phone LIKE ? OR s.name LIKE ? "
                    "ORDER BY ash.animal_id DESC "
                    "LIMIT ? OFFSET ?",
                    (like_query, like_query, per_page, offset),
                ).fetchall()
            ]
            if not page_ids:
                return PaginatedResult(
                    records=(), page=page, per_page=per_page, total_records=total
                )
            placeholders = ",".join("?" for _ in page_ids)
            rows = conn.execute(
                self._JOIN_SQL
                + f"WHERE a.id IN ({placeholders}) ORDER BY a.id DESC, ash.rowid",
                page_ids,
            ).fetchall()
        records = self._hydrate_animals(rows)
        return PaginatedResult(
            records=tuple(records), page=page, per_page=per_page, total_records=total
        )

    # ── FULL EXPORT ──────────────────────────────────────────────────────

    def get_all_for_export(self) -> List[AnimalRecord]:
        with _get_connection(self._db_path) as conn:
            rows = conn.execute(
                self._JOIN_SQL + "ORDER BY a.id, ash.rowid"
            ).fetchall()
        return self._hydrate_animals(rows)
