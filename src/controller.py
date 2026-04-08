"""
controller.py — V2.2 Business-logic layer.

V2.2 changes
-------------
* ``is_paid`` → ``paid_amount`` (Decimal) in all staging and CRUD flows.
* ``toggle_payment`` replaced by ``update_paid_amount``.
* New ``get_dashboard_stats()`` pass-through.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import List, Optional, Tuple

import phonenumbers

from database import KurbanRepository
from models import (
    AnimalRecord,
    DashboardStats,
    PaginatedResult,
    StagedAnimal,
    StagedShareholderEntry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phone validation
# ---------------------------------------------------------------------------

def validate_phone(raw_number: str, region: str = "TR") -> Tuple[bool, str, str]:
    try:
        parsed = phonenumbers.parse(raw_number, region)
    except phonenumbers.NumberParseException as exc:
        return False, f"Telefon numarası ayrıştırılamadı: {exc}", ""

    if not phonenumbers.is_valid_number(parsed):
        return False, "Geçersiz telefon numarası.", ""

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return True, "", e164


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class KurbanController:

    def __init__(self, repo: KurbanRepository) -> None:
        self._repo = repo
        self._staged: List[StagedAnimal] = []

    # ── Staging ──────────────────────────────────────────────────────────

    @property
    def staged_animals(self) -> List[StagedAnimal]:
        return list(self._staged)

    def add_to_staging(
        self,
        slaughter_date: date,
        total_price: Decimal,
        total_weight: Decimal,
        shareholders: List[StagedShareholderEntry],
    ) -> Tuple[bool, str]:
        if not (1 <= len(shareholders) <= 7):
            return False, "Her hayvan için 1–7 arası hissedar gereklidir."
        if total_price <= 0:
            return False, "Toplam fiyat sıfırdan büyük olmalıdır."
        if total_weight <= 0:
            return False, "Toplam ağırlık sıfırdan büyük olmalıdır."

        seen_phones: set[str] = set()
        normalised: List[StagedShareholderEntry] = []

        for sh in shareholders:
            name = sh.name.strip()
            if not name:
                return False, f"Hissedar adı boş bırakılamaz (Tel: {sh.phone})."

            raw = sh.phone.strip()
            if not raw:
                return False, f"Hissedar '{name}': Telefon numarası boş."

            if raw.startswith("+"):
                ok, err, e164 = validate_phone(raw, None)  # type: ignore[arg-type]
            else:
                ok, err, e164 = validate_phone(raw, "TR")
            if not ok:
                return False, f"Hissedar '{name}': {err}"

            if e164 in seen_phones:
                return False, f"Aynı hayvanda '{e164}' birden fazla eklenemez."
            seen_phones.add(e164)

            if not (1 <= sh.share_fraction <= 7):
                return False, f"Hissedar '{name}': Pay 1–7 arasında olmalıdır."

            if sh.paid_amount < 0:
                return False, f"Hissedar '{name}': Ödeme tutarı negatif olamaz."

            normalised.append(
                StagedShareholderEntry(
                    phone=e164, name=name,
                    paid_amount=sh.paid_amount,
                    share_fraction=sh.share_fraction,
                )
            )

        self._staged.append(
            StagedAnimal(slaughter_date, total_price, total_weight, normalised)
        )
        logger.info(
            "Staged animal: date=%s price=%s weight=%s shareholders=%d",
            slaughter_date, total_price, total_weight, len(normalised),
        )
        return True, "Hayvan taslak listeye eklendi."

    def discard_staging(self) -> None:
        count = len(self._staged)
        self._staged.clear()
        logger.info("Discarded %d staged animals", count)

    def commit_staging(self) -> Tuple[bool, str]:
        if not self._staged:
            return False, "Taslak listesinde kayıt yok."
        try:
            ids = self._repo.commit_staged_animals(self._staged)
            count = len(ids)
            self._staged.clear()
            logger.info("Committed %d animals: %s", count, ids)
            return True, f"{count} hayvan başarıyla kaydedildi."
        except Exception as exc:
            logger.exception("Commit failed")
            return False, f"Veritabanı hatası: {exc}"

    # ── Search ───────────────────────────────────────────────────────────

    def search_by_animal_id(self, animal_id: int) -> Optional[AnimalRecord]:
        return self._repo.search_by_animal_id(animal_id)

    def search_by_phone_or_name(self, query: str) -> List[AnimalRecord]:
        return self._repo.search_by_phone_or_name(query)

    # ── Pagination ───────────────────────────────────────────────────────

    def get_animals_paginated(
        self, page: int = 1, per_page: int = 50
    ) -> PaginatedResult:
        return self._repo.get_animals_paginated(page, per_page)

    def search_paginated(
        self, query: str, page: int = 1, per_page: int = 50
    ) -> PaginatedResult:
        return self._repo.search_paginated(query, page, per_page)

    # ── Payment (V2.2) ───────────────────────────────────────────────────

    def update_paid_amount(
        self, animal_id: int, phone: str, paid_amount: Decimal
    ) -> Tuple[bool, str]:
        if paid_amount < 0:
            return False, "Ödeme tutarı negatif olamaz."
        try:
            self._repo.update_paid_amount(animal_id, phone, paid_amount)
            return True, "Ödeme tutarı güncellendi."
        except Exception as exc:
            logger.exception(
                "Payment update failed for animal=%d phone=%s", animal_id, phone
            )
            return False, f"Veritabanı hatası: {exc}"

    # ── Animal update / delete ───────────────────────────────────────────

    def update_animal(
        self,
        animal_id: int,
        slaughter_date: date,
        total_price: Decimal,
        total_weight: Decimal,
    ) -> Tuple[bool, str]:
        try:
            self._repo.update_animal(animal_id, slaughter_date, total_price, total_weight)
            return True, "Hayvan bilgileri güncellendi."
        except Exception as exc:
            logger.exception("Animal update failed for id=%d", animal_id)
            return False, f"Güncelleme hatası: {exc}"

    def delete_animal(self, animal_id: int) -> Tuple[bool, str]:
        try:
            self._repo.delete_animal(animal_id)
            return True, "Hayvan silindi."
        except Exception as exc:
            logger.exception("Animal delete failed for id=%d", animal_id)
            return False, f"Silme hatası: {exc}"

    # ── Share-level CRUD ─────────────────────────────────────────────────

    def add_share_to_animal(
        self,
        animal_id: int,
        raw_phone: str,
        name: str,
        paid_amount: Decimal,
        share_fraction: int,
        region: str = "TR",
    ) -> Tuple[bool, str]:
        name = name.strip()
        if not name:
            return False, "Hissedar adı boş bırakılamaz."
        raw = raw_phone.strip()
        if not raw:
            return False, "Telefon numarası boş."

        if raw.startswith("+"):
            ok, err, e164 = validate_phone(raw, None)  # type: ignore[arg-type]
        else:
            ok, err, e164 = validate_phone(raw, region)
        if not ok:
            return False, err

        if not (1 <= share_fraction <= 7):
            return False, "Pay 1–7 arasında olmalıdır."
        if paid_amount < 0:
            return False, "Ödeme tutarı negatif olamaz."

        try:
            self._repo.add_share_to_animal(
                animal_id, e164, name, paid_amount, share_fraction
            )
            return True, f"Hissedar eklendi: {e164}"
        except Exception as exc:
            logger.exception("Add share failed: animal=%d phone=%s", animal_id, e164)
            return False, f"Hata: {exc}"

    def remove_share_from_animal(
        self, animal_id: int, phone: str
    ) -> Tuple[bool, str]:
        try:
            self._repo.remove_share_from_animal(animal_id, phone)
            return True, "Hissedar kaldırıldı."
        except Exception as exc:
            logger.exception("Remove share failed: animal=%d phone=%s", animal_id, phone)
            return False, f"Hata: {exc}"

    def update_share_in_animal(
        self,
        animal_id: int,
        old_phone: str,
        new_raw_phone: str,
        new_name: str,
        paid_amount: Decimal,
        share_fraction: int,
        region: str = "TR",
    ) -> Tuple[bool, str]:
        new_name = new_name.strip()
        if not new_name:
            return False, "Hissedar adı boş bırakılamaz."
        raw = new_raw_phone.strip()
        if not raw:
            return False, "Telefon numarası boş."

        if raw.startswith("+"):
            ok, err, e164 = validate_phone(raw, None)  # type: ignore[arg-type]
        else:
            ok, err, e164 = validate_phone(raw, region)
        if not ok:
            return False, err

        if not (1 <= share_fraction <= 7):
            return False, "Pay 1–7 arasında olmalıdır."
        if paid_amount < 0:
            return False, "Ödeme tutarı negatif olamaz."

        # Prevent duplicate phone within the same animal (if phone changed)
        if e164 != old_phone:
            rec = self._repo.search_by_animal_id(animal_id)
            if rec:
                existing_phones = {s.phone for s in rec.shares}
                if e164 in existing_phones:
                    return False, f"Bu hayvanda '{e164}' zaten kayıtlı."

        try:
            self._repo.update_share_in_animal(
                animal_id, old_phone, e164, new_name, paid_amount, share_fraction
            )
            return True, f"Hissedar güncellendi: {e164}"
        except Exception as exc:
            logger.exception(
                "Update share failed: animal=%d old=%s new=%s", animal_id, old_phone, e164
            )
            return False, f"Hata: {exc}"

    # ── Dashboard ────────────────────────────────────────────────────────

    def get_dashboard_stats(self) -> DashboardStats:
        return self._repo.get_dashboard_stats()

    # ── Export ────────────────────────────────────────────────────────────

    def get_all_for_export(self) -> List[AnimalRecord]:
        return self._repo.get_all_for_export()
