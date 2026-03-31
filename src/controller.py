"""
controller.py — V2.0 Business-logic layer.

V2.0 changes
-------------
* TC Kimlik validation replaced with ``phonenumbers`` library (E.164).
* ``total_price`` / ``total_weight`` (``Decimal``) added to staging.
* Pagination pass-through methods.
* All operations logged via Python ``logging`` module.
* Payment-toggle returns ``(ok, msg)`` so the GUI can roll back on failure.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

import phonenumbers

from database import KurbanRepository
from models import AnimalRecord, PaginatedResult, StagedAnimal, StagedShareholderEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phone validation
# ---------------------------------------------------------------------------

def validate_phone(raw_number: str, region: str = "TR") -> Tuple[bool, str, str]:
    """Validate a phone number using the ``phonenumbers`` library.

    Parameters
    ----------
    raw_number : str
        The national-significant-number the user typed (e.g. "5551234567").
    region : str
        ISO 3166-1 alpha-2 code selected in the country-code dropdown.

    Returns
    -------
    (valid, error_message, e164)
        On success: ``(True, "", "+905551234567")``.
        On failure: ``(False, "reason", "")``.
    """
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
    """Mediates between the View (GUI) and the Model (Repository)."""

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
        """Validate then append an animal to the staging list.

        Returns ``(success, message)``.
        """
        # --- shareholder count ---
        if not (1 <= len(shareholders) <= 7):
            return False, "Her hayvan için 1–7 arası hissedar gereklidir."

        # --- price / weight ---
        if total_price <= 0:
            return False, "Toplam fiyat sıfırdan büyük olmalıdır."
        if total_weight <= 0:
            return False, "Toplam ağırlık sıfırdan büyük olmalıdır."

        seen_phones: set[str] = set()
        normalised: List[StagedShareholderEntry] = []

        for sh in shareholders:
            # Name
            name = sh.name.strip()
            if not name:
                return False, f"Hissedar adı boş bırakılamaz (Tel: {sh.phone})."

            # Phone validation via phonenumbers — region is embedded
            # in the phone string as "+CC NSN" or just NSN with default TR
            raw = sh.phone.strip()
            if not raw:
                return False, f"Hissedar '{name}': Telefon numarası boş."

            # Detect region from leading "+" or default to TR
            if raw.startswith("+"):
                ok, err, e164 = validate_phone(raw, None)  # type: ignore[arg-type]
            else:
                # If raw already contains region code info set by GUI,
                # it will be in the format stored by the GUI
                ok, err, e164 = validate_phone(raw, "TR")

            if not ok:
                return False, f"Hissedar '{name}': {err}"

            # Duplicate phone inside the same animal
            if e164 in seen_phones:
                return False, f"Aynı hayvanda '{e164}' birden fazla eklenemez."
            seen_phones.add(e164)

            normalised.append(
                StagedShareholderEntry(phone=e164, name=name, is_paid=sh.is_paid)
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
        """Persist every staged animal in one transaction."""
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

    # ── Payment toggle ───────────────────────────────────────────────────

    def toggle_payment(
        self, animal_id: int, phone: str, new_state: bool
    ) -> Tuple[bool, str]:
        """Try to update payment flag.  GUI must roll back on failure."""
        try:
            self._repo.update_payment_status(animal_id, phone, new_state)
            return True, "Ödeme durumu güncellendi."
        except Exception as exc:
            logger.exception("Payment toggle failed for animal=%d phone=%s", animal_id, phone)
            return False, f"Veritabanı hatası — değişiklik geri alındı: {exc}"

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

    # ── Export data ──────────────────────────────────────────────────────

    def get_all_for_export(self) -> List[AnimalRecord]:
        return self._repo.get_all_for_export()
