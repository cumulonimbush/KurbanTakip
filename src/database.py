"""
database.py — V2.2 SQLite schema, connection management, and Repository.

V2.2 changes
-------------
* ``is_paid BOOLEAN`` → ``paid_amount_kurus INTEGER NOT NULL DEFAULT 0``
  in ``animal_shares``.
* Safe V2.1 → V2.2 migration via temp-table rebuild (preserves data:
  ``is_paid=1`` maps to the shareholder's full fractional share price).
* ``_hydrate_animals`` reads ``paid_amount_kurus`` instead of ``is_paid``.
* ``update_payment_status`` replaced by ``update_paid_amount``.
* New ``get_dashboard_stats()`` returning aggregated metrics via SQL.
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

from models import (
    AnimalRecord,
    AnimalShare,
    DashboardStats,
    PaginatedResult,
    StagedAnimal,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Nuitka-safe base path
# ---------------------------------------------------------------------------

def _get_app_dir() -> Path:
    data_dir = Path.home() / "Documents" / "KurbanTakip"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

APP_DIR = _get_app_dir()
DB_FILE = APP_DIR / "kurban.db"

# ---------------------------------------------------------------------------
# Schema  (V2.2 — paid_amount_kurus replaces is_paid)
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
    animal_id           INTEGER NOT NULL,
    phone               TEXT    NOT NULL,
    paid_amount_kurus   INTEGER NOT NULL DEFAULT 0,
    share_fraction      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (animal_id, phone),
    FOREIGN KEY (animal_id) REFERENCES animals(id)         ON DELETE CASCADE,
    FOREIGN KEY (phone)     REFERENCES shareholders(phone) ON DELETE CASCADE
);
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

def _get_column_names(conn: sqlite3.Connection, table: str) -> List[str]:
    return [
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    ]


def initialise_database(db_path: Path = DB_FILE) -> None:
    """Create tables and run safe migrations for V2.0/V2.1 → V2.2."""
    with _get_connection(db_path) as conn:
        # ── Ensure base tables exist ───────────────────────────────────
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        cols = _get_column_names(conn, "animal_shares")

        # ── V2.0 → V2.1 migration: add share_fraction ─────────────────
        if "share_fraction" not in cols and "is_paid" in cols:
            try:
                conn.execute(
                    "ALTER TABLE animal_shares "
                    "ADD COLUMN share_fraction INTEGER NOT NULL DEFAULT 1"
                )
                conn.commit()
                cols.append("share_fraction")
                logger.info("Migration V2.0→V2.1: added share_fraction")
            except sqlite3.OperationalError:
                pass

        # ── V2.1 → V2.2 migration: is_paid → paid_amount_kurus ────────
        if "is_paid" in cols:
            logger.info("Migration V2.1→V2.2: converting is_paid → paid_amount_kurus")
            # We must disable FK checks during the rebuild
            conn.execute("PRAGMA foreign_keys=OFF")

            conn.execute("""\
                CREATE TABLE IF NOT EXISTS animal_shares_new (
                    animal_id           INTEGER NOT NULL,
                    phone               TEXT    NOT NULL,
                    paid_amount_kurus   INTEGER NOT NULL DEFAULT 0,
                    share_fraction      INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (animal_id, phone),
                    FOREIGN KEY (animal_id) REFERENCES animals(id)         ON DELETE CASCADE,
                    FOREIGN KEY (phone)     REFERENCES shareholders(phone) ON DELETE CASCADE
                )
            """)

            # For is_paid=1 rows: calculate the full share price in kurus
            # share_price_kurus = (total_price_kurus * share_fraction)
            #                   / (sum of share_fractions for that animal)
            conn.execute("""\
                INSERT INTO animal_shares_new (animal_id, phone, paid_amount_kurus, share_fraction)
                SELECT
                    ash.animal_id,
                    ash.phone,
                    CASE
                        WHEN ash.is_paid = 1
                        THEN CAST(
                            (a.total_price_kurus * ash.share_fraction * 1.0)
                            / COALESCE(tf.total_frac, ash.share_fraction)
                            AS INTEGER
                        )
                        ELSE 0
                    END,
                    ash.share_fraction
                FROM animal_shares ash
                JOIN animals a ON a.id = ash.animal_id
                LEFT JOIN (
                    SELECT animal_id, SUM(share_fraction) AS total_frac
                    FROM animal_shares
                    GROUP BY animal_id
                ) tf ON tf.animal_id = ash.animal_id
            """)

            conn.execute("DROP TABLE animal_shares")
            conn.execute("ALTER TABLE animal_shares_new RENAME TO animal_shares")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()
            logger.info("Migration V2.1→V2.2 complete: is_paid converted to paid_amount_kurus")

    logger.info("Database initialised at %s", db_path)


# ═══════════════════════════════════════════════════════════════════════════
# Repository
# ═══════════════════════════════════════════════════════════════════════════

class KurbanRepository:

    def __init__(self, db_path: Path = DB_FILE) -> None:
        self._db_path = db_path

    # ── WRITE ────────────────────────────────────────────────────────────

    def commit_staged_animals(self, staged: List[StagedAnimal]) -> List[int]:
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
                            "(animal_id, phone, paid_amount_kurus, share_fraction) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                animal_id,
                                sh.phone,
                                _try_to_kurus(sh.paid_amount),
                                sh.share_fraction,
                            ),
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

    def update_paid_amount(
        self, animal_id: int, phone: str, paid_amount: Decimal
    ) -> None:
        """Update the paid amount (in TRY) for one shareholder."""
        with _get_connection(self._db_path) as conn:
            conn.execute(
                "UPDATE animal_shares SET paid_amount_kurus = ? "
                "WHERE animal_id = ? AND phone = ?",
                (_try_to_kurus(paid_amount), animal_id, phone),
            )
            conn.commit()
        logger.info(
            "Paid amount updated: animal=%d phone=%s amount=%s",
            animal_id, phone, paid_amount,
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
        with _get_connection(self._db_path) as conn:
            conn.execute("DELETE FROM animals WHERE id = ?", (animal_id,))
            conn.commit()
        logger.info("Animal %d deleted (cascaded shares)", animal_id)

    # ── Share-level CRUD ─────────────────────────────────────────────────

    def add_share_to_animal(
        self,
        animal_id: int,
        phone: str,
        name: str,
        paid_amount: Decimal,
        share_fraction: int = 1,
    ) -> None:
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
                    "(animal_id, phone, paid_amount_kurus, share_fraction) "
                    "VALUES (?, ?, ?, ?)",
                    (animal_id, phone, _try_to_kurus(paid_amount), share_fraction),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        logger.info(
            "Share added: animal=%d phone=%s fraction=%d paid=%s",
            animal_id, phone, share_fraction, paid_amount,
        )

    def remove_share_from_animal(self, animal_id: int, phone: str) -> None:
        with _get_connection(self._db_path) as conn:
            conn.execute(
                "DELETE FROM animal_shares WHERE animal_id = ? AND phone = ?",
                (animal_id, phone),
            )
            conn.commit()
        logger.info("Share removed: animal=%d phone=%s", animal_id, phone)

    def update_share_in_animal(
        self,
        animal_id: int,
        old_phone: str,
        new_phone: str,
        new_name: str,
        paid_amount: Decimal,
        share_fraction: int,
    ) -> None:
        """Update an existing share.  Handles phone (PK) change safely."""
        with _get_connection(self._db_path) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("BEGIN")
                # Upsert the (possibly new) shareholder record
                cursor.execute(
                    "INSERT INTO shareholders (phone, name) VALUES (?, ?) "
                    "ON CONFLICT(phone) DO UPDATE SET name=excluded.name",
                    (new_phone, new_name),
                )
                if old_phone == new_phone:
                    # Simple in-place update — no PK change
                    cursor.execute(
                        "UPDATE animal_shares "
                        "SET paid_amount_kurus = ?, share_fraction = ? "
                        "WHERE animal_id = ? AND phone = ?",
                        (_try_to_kurus(paid_amount), share_fraction,
                         animal_id, old_phone),
                    )
                else:
                    # Phone changed → delete old row, insert new one
                    cursor.execute(
                        "DELETE FROM animal_shares "
                        "WHERE animal_id = ? AND phone = ?",
                        (animal_id, old_phone),
                    )
                    cursor.execute(
                        "INSERT INTO animal_shares "
                        "(animal_id, phone, paid_amount_kurus, share_fraction) "
                        "VALUES (?, ?, ?, ?)",
                        (animal_id, new_phone,
                         _try_to_kurus(paid_amount), share_fraction),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        logger.info(
            "Share updated: animal=%d old_phone=%s new_phone=%s fraction=%d paid=%s",
            animal_id, old_phone, new_phone, share_fraction, paid_amount,
        )

    # ── READ ─────────────────────────────────────────────────────────────

    _JOIN_SQL = (
        "SELECT a.id, a.slaughter_date, "
        "       a.total_price_kurus, a.total_weight_grams, "
        "       ash.phone, s.name, ash.paid_amount_kurus, ash.share_fraction "
        "FROM animals a "
        "JOIN animal_shares ash ON ash.animal_id = a.id "
        "JOIN shareholders  s   ON s.phone = ash.phone "
    )

    def _hydrate_animals(self, rows: list) -> List[AnimalRecord]:
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
                    paid_amount=_kurus_to_try(row["paid_amount_kurus"]),
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

    # ── DASHBOARD STATS (pure SQL, no record hydration) ──────────────────

    def get_dashboard_stats(self) -> DashboardStats:
        """Efficient SQL aggregation — never loads full records into memory."""
        with _get_connection(self._db_path) as conn:
            # Total animals
            r1 = conn.execute("SELECT COUNT(*) AS cnt FROM animals").fetchone()
            total_animals = r1["cnt"] if r1 else 0

            # Expected revenue
            r2 = conn.execute(
                "SELECT COALESCE(SUM(total_price_kurus), 0) AS total FROM animals"
            ).fetchone()
            expected_revenue_kurus = r2["total"]

            # Share-level aggregates
            r3 = conn.execute(
                "SELECT "
                "  COUNT(*)                            AS sold_shares, "
                "  COALESCE(SUM(share_fraction), 0)    AS total_fracs, "
                "  COALESCE(SUM(paid_amount_kurus), 0) AS collected "
                "FROM animal_shares"
            ).fetchone()
            sold_shares = r3["sold_shares"]
            total_fractions_sold = r3["total_fracs"]
            collected_amount_kurus = r3["collected"]

        capacity = total_animals * 7
        unsold = capacity - sold_shares

        return DashboardStats(
            total_animals=total_animals,
            total_share_capacity=capacity,
            sold_shares=sold_shares,
            total_fractions_sold=total_fractions_sold,
            expected_revenue_kurus=expected_revenue_kurus,
            collected_amount_kurus=collected_amount_kurus,
            unsold_shares=max(0, unsold),
        )
