"""
gui.py — V2.1 PyQt6 interface for the Kurban Tracking Application.

V2.1 changes
-------------
* **Stylesheet extracted** to ``style.qss`` — no ``_STYLESHEET`` string here.
* **Dynamic country codes** from ``phonenumbers.SUPPORTED_REGIONS``
  (TR pinned at top, rest alphabetical).
* **Fractional shares** via ``QSpinBox`` (1–7) in Registration and Edit Dialog.
* **Full CRUD in Edit Dialog**: "Delete Animal", "Add Shareholder",
  "Remove Selected Shareholder" buttons.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional, Tuple

import phonenumbers

from PyQt6.QtCore import QDate, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backup import create_backup
from controller import KurbanController
from export import generate_excel_report
from models import AnimalRecord, StagedShareholderEntry

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Dynamic country-code list from phonenumbers
# ═══════════════════════════════════════════════════════════════════════════

def _build_country_codes() -> List[Tuple[str, str, str]]:
    """Return ``[(iso, dial_code, display_label), ...]``.

    TR is pinned at the top; everything else is alphabetical by ISO code.
    """
    entries: List[Tuple[str, str, str]] = []
    for region in sorted(phonenumbers.SUPPORTED_REGIONS):
        code = phonenumbers.country_code_for_region(region)
        label = f"{region} (+{code})"
        entries.append((region, f"+{code}", label))

    # Pin TR at top
    tr_entry: Optional[Tuple[str, str, str]] = None
    rest: List[Tuple[str, str, str]] = []
    for entry in entries:
        if entry[0] == "TR":
            tr_entry = entry
        else:
            rest.append(entry)

    if tr_entry:
        return [tr_entry] + rest
    return rest


COUNTRY_CODES = _build_country_codes()


# ═══════════════════════════════════════════════════════════════════════════
# Export worker (background thread)
# ═══════════════════════════════════════════════════════════════════════════

class ExportWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(
        self, records: List[AnimalRecord], dest: Path, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._records = records
        self._dest = dest

    def run(self) -> None:
        try:
            generate_excel_report(self._records, self._dest)
            self.finished.emit(True, f"Rapor başarıyla dışa aktarıldı:\n{self._dest}")
        except Exception as exc:
            logger.exception("Export worker failed")
            self.finished.emit(False, f"Dışa aktarma hatası: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Shareholder input row widget
# ═══════════════════════════════════════════════════════════════════════════

class ShareholderRow(QWidget):
    """[idx] [country] [phone] [name] [fraction spinbox] [paid] [✕]"""

    remove_requested = pyqtSignal(object)

    def __init__(self, index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.index = index
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        self.lbl_idx = QLabel(str(index))
        self.lbl_idx.setFixedWidth(20)
        self.lbl_idx.setStyleSheet("color: #64748B; font-weight: 600;")
        layout.addWidget(self.lbl_idx)

        self.combo_country = QComboBox()
        self.combo_country.setMinimumWidth(140)
        for _iso, _code, _label in COUNTRY_CODES:
            self.combo_country.addItem(_label, _iso)
        layout.addWidget(self.combo_country)

        self.edit_phone = QLineEdit()
        self.edit_phone.setPlaceholderText("5XX XXX XX XX")
        self.edit_phone.setMinimumWidth(130)
        layout.addWidget(self.edit_phone)

        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Ad Soyad")
        self.edit_name.setMinimumWidth(140)
        layout.addWidget(self.edit_name, stretch=1)

        self.spin_fraction = QSpinBox()
        self.spin_fraction.setRange(1, 7)
        self.spin_fraction.setValue(1)
        self.spin_fraction.setPrefix("Pay: ")
        self.spin_fraction.setFixedWidth(85)
        self.spin_fraction.setToolTip("Hisse payı (1–7)")
        layout.addWidget(self.spin_fraction)

        self.cb_paid = QCheckBox("Ödendi")
        layout.addWidget(self.cb_paid)

        btn_remove = QPushButton("✕")
        btn_remove.setObjectName("btnRemove")
        btn_remove.setFixedSize(28, 28)
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_remove.setToolTip("Bu hissedarı kaldır")
        btn_remove.clicked.connect(lambda: self.remove_requested.emit(self))
        layout.addWidget(btn_remove)

    @property
    def region(self) -> str:
        return self.combo_country.currentData()

    def to_entry(self) -> StagedShareholderEntry:
        raw = self.edit_phone.text().strip()
        idx = self.combo_country.currentIndex()
        code = COUNTRY_CODES[idx][1]
        full_phone = f"{code}{raw}" if raw and not raw.startswith("+") else raw
        return StagedShareholderEntry(
            phone=full_phone,
            name=self.edit_name.text().strip(),
            is_paid=self.cb_paid.isChecked(),
            share_fraction=self.spin_fraction.value(),
        )

    def set_index(self, idx: int) -> None:
        self.index = idx
        self.lbl_idx.setText(str(idx))


# ═══════════════════════════════════════════════════════════════════════════
# Edit modal dialog  (V2.1 — Full CRUD)
# ═══════════════════════════════════════════════════════════════════════════

class AnimalEditDialog(QDialog):
    """Modal for editing an animal: update fields, toggle payments,
    add/remove shareholders, or delete the whole animal."""

    def __init__(
        self, record: AnimalRecord, ctrl: KurbanController, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._record = record
        self._ctrl = ctrl
        self._changed = False
        self._deleted = False

        self.setWindowTitle(f"Hayvan #{record.animal_id} — Düzenle")
        self.setMinimumWidth(780)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Animal info ────────────────────────────────────────────────
        info_group = QGroupBox("Hayvan Bilgileri")
        ig = QGridLayout(info_group)

        ig.addWidget(QLabel("Hayvan ID:"), 0, 0)
        ig.addWidget(QLabel(str(record.animal_id)), 0, 1)

        ig.addWidget(QLabel("Kesim Tarihi:"), 1, 0)
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("dd.MM.yyyy")
        self._date_edit.setDate(
            QDate(record.slaughter_date.year, record.slaughter_date.month, record.slaughter_date.day)
        )
        ig.addWidget(self._date_edit, 1, 1)

        ig.addWidget(QLabel("Toplam Fiyat (₺):"), 2, 0)
        self._price_edit = QLineEdit(str(record.total_price))
        ig.addWidget(self._price_edit, 2, 1)

        ig.addWidget(QLabel("Toplam Ağırlık (kg):"), 3, 0)
        self._weight_edit = QLineEdit(str(record.total_weight))
        ig.addWidget(self._weight_edit, 3, 1)

        ig.addWidget(QLabel("Toplam Pay:"), 4, 0)
        self._frac_label = QLabel(str(record.total_fractions))
        self._frac_label.setStyleSheet("color: #F59E0B; font-weight: bold;")
        ig.addWidget(self._frac_label, 4, 1)

        ig.addWidget(QLabel("Birim Pay Fiyat:"), 5, 0)
        self._per_price_lbl = QLabel(f"{record.price_per_unit_fraction} ₺")
        self._per_price_lbl.setStyleSheet("color: #22C55E; font-weight: bold;")
        ig.addWidget(self._per_price_lbl, 5, 1)

        ig.addWidget(QLabel("Birim Pay Ağırlık:"), 6, 0)
        self._per_weight_lbl = QLabel(f"{record.weight_per_unit_fraction} kg")
        self._per_weight_lbl.setStyleSheet("color: #22C55E; font-weight: bold;")
        ig.addWidget(self._per_weight_lbl, 6, 1)

        layout.addWidget(info_group)

        # ── Shareholders table ─────────────────────────────────────────
        sh_group = QGroupBox(f"Hissedarlar ({len(record.shares)})")
        self._sh_group = sh_group
        sl = QVBoxLayout(sh_group)

        self._sh_table = QTableWidget(len(record.shares), 6)
        self._sh_table.setHorizontalHeaderLabels(
            ["Telefon", "Ad Soyad", "Pay", "Hisse Fiyat", "Ödendi", "Seç"]
        )
        hh = self._sh_table.horizontalHeader()
        assert hh is not None
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._sh_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        self._populate_sh_table()
        sl.addWidget(self._sh_table)

        # Add / Remove shareholder buttons
        sh_btn_row = QHBoxLayout()
        btn_add_sh = QPushButton("➕  Hissedar Ekle")
        btn_add_sh.setObjectName("btnSmall")
        btn_add_sh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_sh.clicked.connect(self._on_add_shareholder)
        sh_btn_row.addWidget(btn_add_sh)

        btn_remove_sh = QPushButton("🗑  Seçili Hissedarı Sil")
        btn_remove_sh.setObjectName("btnRemove")
        btn_remove_sh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_remove_sh.clicked.connect(self._on_remove_shareholder)
        sh_btn_row.addWidget(btn_remove_sh)

        sh_btn_row.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )
        sl.addLayout(sh_btn_row)
        layout.addWidget(sh_group)

        # ── Bottom buttons ─────────────────────────────────────────────
        bottom = QHBoxLayout()

        btn_delete = QPushButton("🗑  Bu Hayvanı Komple Sil")
        btn_delete.setObjectName("btnDeleteAnimal")
        btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_delete.clicked.connect(self._on_delete_animal)
        bottom.addWidget(btn_delete)

        bottom.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        )

        btn_save = QPushButton("💾  Kaydet")
        btn_save.setObjectName("btnSuccess")
        btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.clicked.connect(self._on_save)
        bottom.addWidget(btn_save)

        btn_cancel = QPushButton("İptal")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        bottom.addWidget(btn_cancel)

        layout.addLayout(bottom)

    # -- populate shareholders table --

    def _populate_sh_table(self) -> None:
        rec = self._record
        self._sh_table.setRowCount(len(rec.shares))
        tf = rec.total_fractions

        for row, share in enumerate(rec.shares):
            self._sh_table.setItem(row, 0, QTableWidgetItem(share.phone))
            self._sh_table.setItem(row, 1, QTableWidgetItem(share.shareholder_name))
            self._sh_table.setItem(row, 2, QTableWidgetItem(str(share.share_fraction)))

            sh_price = share.price_for(rec.total_price, tf)
            price_item = QTableWidgetItem(f"{sh_price} ₺")
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._sh_table.setItem(row, 3, price_item)

            # Payment checkbox with rollback
            cb = QCheckBox()
            cb.setChecked(share.is_paid)
            cb.setStyleSheet("margin-left: 18px;")
            cb.setProperty("animal_id", rec.animal_id)
            cb.setProperty("phone", share.phone)
            cb.stateChanged.connect(lambda _, c=cb: self._on_toggle(c))
            wrapper = QWidget()
            wl = QHBoxLayout(wrapper)
            wl.addWidget(cb)
            wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            wl.setContentsMargins(0, 0, 0, 0)
            self._sh_table.setCellWidget(row, 4, wrapper)

            # Select checkbox for removal
            sel_cb = QCheckBox()
            sel_cb.setProperty("phone", share.phone)
            sel_wrapper = QWidget()
            sel_layout = QHBoxLayout(sel_wrapper)
            sel_layout.addWidget(sel_cb)
            sel_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sel_layout.setContentsMargins(0, 0, 0, 0)
            self._sh_table.setCellWidget(row, 5, sel_wrapper)

            # Row tint
            tint = QColor("#0B3D1A") if share.is_paid else QColor("#3D0B0B")
            for c in (0, 1, 2, 3):
                itm = self._sh_table.item(row, c)
                if itm:
                    itm.setBackground(tint)

    def _refresh_record(self) -> None:
        """Re-fetch the record from DB and repopulate."""
        updated = self._ctrl.search_by_animal_id(self._record.animal_id)
        if updated is None:
            return
        self._record = updated
        self._populate_sh_table()
        self._sh_group.setTitle(f"Hissedarlar ({len(updated.shares)})")
        self._frac_label.setText(str(updated.total_fractions))
        self._per_price_lbl.setText(f"{updated.price_per_unit_fraction} ₺")
        self._per_weight_lbl.setText(f"{updated.weight_per_unit_fraction} kg")

    # -- slots --

    def _on_toggle(self, checkbox: QCheckBox) -> None:
        animal_id: int = checkbox.property("animal_id")
        phone: str = checkbox.property("phone")
        new_state: bool = checkbox.isChecked()

        ok, msg = self._ctrl.toggle_payment(animal_id, phone, new_state)
        if not ok:
            checkbox.blockSignals(True)
            checkbox.setChecked(not new_state)
            checkbox.blockSignals(False)
            QMessageBox.critical(self, "Veritabanı Hatası", msg)
        else:
            self._changed = True
            self._refresh_record()

    def _on_save(self) -> None:
        try:
            price = Decimal(self._price_edit.text().strip().replace(",", "."))
            weight = Decimal(self._weight_edit.text().strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            QMessageBox.warning(self, "Hata", "Fiyat veya ağırlık geçersiz.")
            return

        if price <= 0 or weight <= 0:
            QMessageBox.warning(self, "Hata", "Fiyat ve ağırlık sıfırdan büyük olmalı.")
            return

        qd = self._date_edit.date()
        sdate = date(qd.year(), qd.month(), qd.day())

        ok, msg = self._ctrl.update_animal(self._record.animal_id, sdate, price, weight)
        if ok:
            self._changed = True
            self.accept()
        else:
            QMessageBox.critical(self, "Hata", msg)

    def _on_delete_animal(self) -> None:
        reply = QMessageBox.warning(
            self,
            "Hayvanı Sil",
            f"Hayvan #{self._record.animal_id} ve tüm hissedarları kalıcı olarak silinecek.\n\n"
            "Bu işlem geri alınamaz. Emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ok, msg = self._ctrl.delete_animal(self._record.animal_id)
        if ok:
            self._changed = True
            self._deleted = True
            self.accept()
        else:
            QMessageBox.critical(self, "Hata", msg)

    def _on_add_shareholder(self) -> None:
        if len(self._record.shares) >= 7:
            QMessageBox.information(self, "Bilgi", "Maksimum 7 hissedar eklenebilir.")
            return

        dlg = _AddShareholderDialog(self._record.animal_id, self._ctrl, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._changed = True
            self._refresh_record()

    def _on_remove_shareholder(self) -> None:
        selected_phones: List[str] = []
        for row in range(self._sh_table.rowCount()):
            wrapper = self._sh_table.cellWidget(row, 5)
            if wrapper:
                cb = wrapper.findChild(QCheckBox)
                if cb and cb.isChecked():
                    phone = cb.property("phone")
                    if phone:
                        selected_phones.append(phone)

        if not selected_phones:
            QMessageBox.information(self, "Bilgi", "Lütfen silmek istediğiniz hissedarları seçin.")
            return

        remaining = len(self._record.shares) - len(selected_phones)
        if remaining < 1:
            QMessageBox.warning(self, "Uyarı", "En az 1 hissedar kalmalıdır.")
            return

        reply = QMessageBox.question(
            self,
            "Hissedar Sil",
            f"{len(selected_phones)} hissedar silinecek. Emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for phone in selected_phones:
            ok, msg = self._ctrl.remove_share_from_animal(self._record.animal_id, phone)
            if not ok:
                QMessageBox.critical(self, "Hata", msg)
                break

        self._changed = True
        self._refresh_record()

    @property
    def data_changed(self) -> bool:
        return self._changed

    @property
    def animal_deleted(self) -> bool:
        return self._deleted


# ═══════════════════════════════════════════════════════════════════════════
# Add-Shareholder sub-dialog
# ═══════════════════════════════════════════════════════════════════════════

class _AddShareholderDialog(QDialog):
    """Small dialog to add one shareholder to an existing animal."""

    def __init__(
        self, animal_id: int, ctrl: KurbanController, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._animal_id = animal_id
        self._ctrl = ctrl

        self.setWindowTitle("Hissedar Ekle")
        self.setMinimumWidth(460)

        layout = QGridLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Ülke Kodu:"), 0, 0)
        self._combo_country = QComboBox()
        for _iso, _code, _label in COUNTRY_CODES:
            self._combo_country.addItem(_label, _iso)
        layout.addWidget(self._combo_country, 0, 1)

        layout.addWidget(QLabel("Telefon:"), 1, 0)
        self._edit_phone = QLineEdit()
        self._edit_phone.setPlaceholderText("5XX XXX XX XX")
        layout.addWidget(self._edit_phone, 1, 1)

        layout.addWidget(QLabel("Ad Soyad:"), 2, 0)
        self._edit_name = QLineEdit()
        layout.addWidget(self._edit_name, 2, 1)

        layout.addWidget(QLabel("Pay (Fraction):"), 3, 0)
        self._spin_frac = QSpinBox()
        self._spin_frac.setRange(1, 7)
        self._spin_frac.setValue(1)
        layout.addWidget(self._spin_frac, 3, 1)

        layout.addWidget(QLabel("Ödendi:"), 4, 0)
        self._cb_paid = QCheckBox()
        layout.addWidget(self._cb_paid, 4, 1)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box, 5, 0, 1, 2)

    def _on_ok(self) -> None:
        raw = self._edit_phone.text().strip()
        idx = self._combo_country.currentIndex()
        code = COUNTRY_CODES[idx][1]
        full_phone = f"{code}{raw}" if raw and not raw.startswith("+") else raw
        region = self._combo_country.currentData()

        ok, msg = self._ctrl.add_share_to_animal(
            animal_id=self._animal_id,
            raw_phone=full_phone,
            name=self._edit_name.text().strip(),
            is_paid=self._cb_paid.isChecked(),
            share_fraction=self._spin_frac.value(),
            region=region,
        )
        if ok:
            self.accept()
        else:
            QMessageBox.warning(self, "Hata", msg)


# ═══════════════════════════════════════════════════════════════════════════
# Main window
# ═══════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self, controller: KurbanController) -> None:
        super().__init__()
        self._ctrl = controller
        self._export_worker: Optional[ExportWorker] = None

        self._search_query: str = ""
        self._current_page: int = 1
        self._per_page: int = 50

        self.setWindowTitle("Kurban Takip Sistemi")
        self.setMinimumSize(1140, 760)
        self.resize(1260, 820)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 8)

        title = QLabel("🐄  Kurban Takip Sistemi  —  V2.1")
        title.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #F8FAFC; padding: 4px 0 8px 0;")
        root.addWidget(title)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs)

        self._tabs.addTab(self._build_registration_tab(), "📋  Kayıt")
        self._tabs.addTab(self._build_search_tab(), "🔍  Kontrol")
        self._tabs.addTab(self._build_export_tab(), "📊  Dışa Aktar")

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Hazır.")

    def closeEvent(self, event) -> None:
        result = create_backup()
        if result:
            logger.info("Backup on close: %s", result)
        super().closeEvent(event)

    # ═══════════════════════════════════════════════════════════════════
    #  TAB 1 — Registration
    # ═══════════════════════════════════════════════════════════════════

    def _build_registration_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Animal form
        form_group = QGroupBox("Yeni Hayvan Bilgileri")
        fg = QGridLayout(form_group)
        fg.setSpacing(10)

        fg.addWidget(QLabel("Kesim Tarihi:"), 0, 0)
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.setDisplayFormat("dd.MM.yyyy")
        fg.addWidget(self._date_edit, 0, 1)

        fg.addWidget(QLabel("Toplam Fiyat (₺):"), 0, 2)
        self._price_input = QLineEdit()
        self._price_input.setPlaceholderText("ör. 21000.00")
        fg.addWidget(self._price_input, 0, 3)

        fg.addWidget(QLabel("Toplam Ağırlık (kg):"), 1, 0)
        self._weight_input = QLineEdit()
        self._weight_input.setPlaceholderText("ör. 350.500")
        fg.addWidget(self._weight_input, 1, 1)

        layout.addWidget(form_group)

        # Dynamic shareholder rows
        sh_group = QGroupBox("Hissedarlar")
        sh_outer = QVBoxLayout(sh_group)

        btn_bar = QHBoxLayout()
        self._btn_add_sh = QPushButton("➕  Hissedar Ekle")
        self._btn_add_sh.setObjectName("btnSmall")
        self._btn_add_sh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add_sh.clicked.connect(self._add_shareholder_row)
        btn_bar.addWidget(self._btn_add_sh)
        self._sh_count_label = QLabel("0 / 7")
        self._sh_count_label.setStyleSheet("color: #94A3B8; font-weight: 600;")
        btn_bar.addWidget(self._sh_count_label)
        btn_bar.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        sh_outer.addLayout(btn_bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self._sh_container = QWidget()
        self._sh_rows_layout = QVBoxLayout(self._sh_container)
        self._sh_rows_layout.setSpacing(4)
        self._sh_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._sh_rows_layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )
        scroll.setWidget(self._sh_container)
        sh_outer.addWidget(scroll)
        layout.addWidget(sh_group)

        self._sh_rows: List[ShareholderRow] = []
        self._add_shareholder_row()

        # Add animal button
        btn_add = QPushButton("➕  Hayvan Ekle (Taslaklara)")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(self._on_add_animal)
        layout.addWidget(btn_add, alignment=Qt.AlignmentFlag.AlignRight)

        # Staging table
        stage_group = QGroupBox("Taslak Listesi")
        stl = QVBoxLayout(stage_group)

        self._staging_table = QTableWidget(0, 4)
        self._staging_table.setHorizontalHeaderLabels(
            ["#", "Kesim Tarihi", "Fiyat / Ağırlık", "Hissedarlar"]
        )
        h = self._staging_table.horizontalHeader()
        assert h is not None
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._staging_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        stl.addWidget(self._staging_table)

        bottom = QHBoxLayout()
        bottom.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        btn_discard = QPushButton("🗑  Taslağı Temizle")
        btn_discard.setObjectName("btnDanger")
        btn_discard.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_discard.clicked.connect(self._on_discard)
        bottom.addWidget(btn_discard)

        btn_commit = QPushButton("💾  Tümünü Kaydet")
        btn_commit.setObjectName("btnSuccess")
        btn_commit.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_commit.clicked.connect(self._on_commit)
        bottom.addWidget(btn_commit)

        stl.addLayout(bottom)
        layout.addWidget(stage_group)

        return tab

    # -- dynamic shareholder helpers --

    def _add_shareholder_row(self) -> None:
        if len(self._sh_rows) >= 7:
            QMessageBox.information(self, "Bilgi", "Maksimum 7 hissedar eklenebilir.")
            return
        row = ShareholderRow(len(self._sh_rows) + 1)
        row.remove_requested.connect(self._remove_shareholder_row)
        self._sh_rows_layout.insertWidget(self._sh_rows_layout.count() - 1, row)
        self._sh_rows.append(row)
        self._update_sh_counter()

    def _remove_shareholder_row(self, row: ShareholderRow) -> None:
        if len(self._sh_rows) <= 1:
            QMessageBox.information(self, "Bilgi", "En az 1 hissedar gereklidir.")
            return
        self._sh_rows.remove(row)
        self._sh_rows_layout.removeWidget(row)
        row.deleteLater()
        for i, r in enumerate(self._sh_rows):
            r.set_index(i + 1)
        self._update_sh_counter()

    def _update_sh_counter(self) -> None:
        self._sh_count_label.setText(f"{len(self._sh_rows)} / 7")
        self._btn_add_sh.setEnabled(len(self._sh_rows) < 7)

    def _on_add_animal(self) -> None:
        try:
            price = Decimal(self._price_input.text().strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            QMessageBox.warning(self, "Hata", "Geçersiz fiyat değeri.")
            return
        try:
            weight = Decimal(self._weight_input.text().strip().replace(",", "."))
        except (InvalidOperation, ValueError):
            QMessageBox.warning(self, "Hata", "Geçersiz ağırlık değeri.")
            return

        qd = self._date_edit.date()
        sdate = date(qd.year(), qd.month(), qd.day())
        shareholders = [r.to_entry() for r in self._sh_rows]

        ok, msg = self._ctrl.add_to_staging(sdate, price, weight, shareholders)
        if not ok:
            QMessageBox.warning(self, "Doğrulama Hatası", msg)
            return

        self._refresh_staging_table()
        self._clear_reg_form()
        self._status_bar.showMessage(msg, 5000)

    def _clear_reg_form(self) -> None:
        self._price_input.clear()
        self._weight_input.clear()
        while len(self._sh_rows) > 1:
            r = self._sh_rows[-1]
            self._sh_rows.remove(r)
            self._sh_rows_layout.removeWidget(r)
            r.deleteLater()
        if self._sh_rows:
            first = self._sh_rows[0]
            first.edit_phone.clear()
            first.edit_name.clear()
            first.cb_paid.setChecked(False)
            first.spin_fraction.setValue(1)
            first.combo_country.setCurrentIndex(0)
        self._update_sh_counter()

    def _refresh_staging_table(self) -> None:
        staged = self._ctrl.staged_animals
        self._staging_table.setRowCount(len(staged))
        for row, a in enumerate(staged):
            self._staging_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self._staging_table.setItem(row, 1, QTableWidgetItem(a.slaughter_date.isoformat()))
            self._staging_table.setItem(
                row, 2, QTableWidgetItem(f"{a.total_price} ₺ / {a.total_weight} kg")
            )
            parts = [
                f"{sh.name} x{sh.share_fraction} ({'✅' if sh.is_paid else '❌'})"
                for sh in a.shareholders
            ]
            self._staging_table.setItem(row, 3, QTableWidgetItem(" | ".join(parts)))

    def _on_discard(self) -> None:
        if not self._ctrl.staged_animals:
            return
        reply = QMessageBox.question(
            self, "Taslağı Temizle",
            "Tüm taslak kayıtlar silinecek. Emin misiniz?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._ctrl.discard_staging()
            self._refresh_staging_table()
            self._status_bar.showMessage("Taslak temizlendi.", 5000)

    def _on_commit(self) -> None:
        ok, msg = self._ctrl.commit_staging()
        if ok:
            self._refresh_staging_table()
            self._status_bar.showMessage(msg, 5000)
            QMessageBox.information(self, "Başarılı", msg)
        else:
            QMessageBox.critical(self, "Hata", msg)

    # ═══════════════════════════════════════════════════════════════════
    #  TAB 2 — Search & Control  (paginated)
    # ═══════════════════════════════════════════════════════════════════

    def _build_search_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        sg = QGroupBox("Arama")
        sgl = QHBoxLayout(sg)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            "Hayvan ID / Telefon / İsim giriniz… (boş = tümü)"
        )
        self._search_input.returnPressed.connect(self._on_search)
        sgl.addWidget(self._search_input, 3)

        btn_search = QPushButton("🔍  Ara")
        btn_search.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_search.clicked.connect(self._on_search)
        sgl.addWidget(btn_search)

        layout.addWidget(sg)

        rg = QGroupBox("Sonuçlar")
        rgl = QVBoxLayout(rg)

        self._results_table = QTableWidget(0, 7)
        self._results_table.setHorizontalHeaderLabels(
            ["Hayvan ID", "Kesim Tarihi", "Fiyat (₺)", "Ağırlık (kg)",
             "Hissedar", "Toplam Pay", "Durum"]
        )
        rh = self._results_table.horizontalHeader()
        assert rh is not None
        for i in range(7):
            rh.setSectionResizeMode(
                i,
                QHeaderView.ResizeMode.Stretch if i == 6
                else QHeaderView.ResizeMode.ResizeToContents,
            )
        self._results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._results_table.doubleClicked.connect(self._on_result_double_click)
        rgl.addWidget(self._results_table)

        pbar = QHBoxLayout()
        self._btn_prev = QPushButton("◀  Önceki")
        self._btn_prev.setObjectName("btnSmall")
        self._btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_prev.clicked.connect(self._on_prev_page)
        pbar.addWidget(self._btn_prev)

        self._page_label = QLabel("Sayfa 1 / 1")
        self._page_label.setStyleSheet("color: #94A3B8; font-weight: 600; font-size: 12px;")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pbar.addWidget(self._page_label, stretch=1)

        self._btn_next = QPushButton("Sonraki  ▶")
        self._btn_next.setObjectName("btnSmall")
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(self._on_next_page)
        pbar.addWidget(self._btn_next)

        rgl.addLayout(pbar)

        hint = QLabel("💡  Düzenlemek için satıra çift tıklayın.")
        hint.setStyleSheet("color: #64748B; font-size: 11px; padding: 2px;")
        rgl.addWidget(hint)

        layout.addWidget(rg)
        return tab

    def _on_search(self) -> None:
        self._search_query = self._search_input.text().strip()
        self._current_page = 1
        self._load_page()

    def _on_prev_page(self) -> None:
        if self._current_page > 1:
            self._current_page -= 1
            self._load_page()

    def _on_next_page(self) -> None:
        self._current_page += 1
        self._load_page()

    def _load_page(self) -> None:
        query = self._search_query

        if query and query.isdigit():
            rec = self._ctrl.search_by_animal_id(int(query))
            records = [rec] if rec else []
            self._populate_results_table(records, 1, 1)
            return

        if query:
            pr = self._ctrl.search_paginated(query, self._current_page, self._per_page)
        else:
            pr = self._ctrl.get_animals_paginated(self._current_page, self._per_page)

        self._current_page = pr.page
        self._populate_results_table(list(pr.records), pr.page, pr.total_pages)

        total = pr.total_records
        shown = len(pr.records)
        if total == 0:
            self._status_bar.showMessage("Sonuç bulunamadı.", 5000)
        else:
            self._status_bar.showMessage(
                f"{total} hayvan, {shown} gösteriliyor (sayfa {pr.page}/{pr.total_pages}).",
                5000,
            )

    def _populate_results_table(
        self, records: List[AnimalRecord], page: int, total_pages: int
    ) -> None:
        self._results_table.setRowCount(len(records))
        for row, rec in enumerate(records):
            _c = lambda v, align=True: self._make_centered_item(str(v))

            id_item = _c(rec.animal_id)
            id_item.setData(Qt.ItemDataRole.UserRole, rec)
            self._results_table.setItem(row, 0, id_item)
            self._results_table.setItem(row, 1, _c(rec.slaughter_date.strftime("%d.%m.%Y")))
            self._results_table.setItem(row, 2, _c(rec.total_price))
            self._results_table.setItem(row, 3, _c(rec.total_weight))
            self._results_table.setItem(row, 4, _c(rec.share_count))
            self._results_table.setItem(row, 5, _c(rec.total_fractions))

            paid = sum(1 for s in rec.shares if s.is_paid)
            status_text = f"{paid}/{rec.share_count} ödendi"
            st_item = _c(status_text)
            if paid == rec.share_count:
                st_item.setForeground(QColor("#22C55E"))
            elif paid == 0:
                st_item.setForeground(QColor("#EF4444"))
            else:
                st_item.setForeground(QColor("#F59E0B"))
            self._results_table.setItem(row, 6, st_item)

        self._page_label.setText(f"Sayfa {page} / {total_pages}")
        self._btn_prev.setEnabled(page > 1)
        self._btn_next.setEnabled(page < total_pages)

    @staticmethod
    def _make_centered_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _on_result_double_click(self, index) -> None:
        row = index.row()
        id_item = self._results_table.item(row, 0)
        if not id_item:
            return
        record: AnimalRecord = id_item.data(Qt.ItemDataRole.UserRole)
        if not record:
            return

        dialog = AnimalEditDialog(record, self._ctrl, self)
        dialog.exec()
        if dialog.data_changed:
            self._load_page()

    # ═══════════════════════════════════════════════════════════════════
    #  TAB 3 — Export
    # ═══════════════════════════════════════════════════════════════════

    def _build_export_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)
        layout.setContentsMargins(40, 40, 40, 40)

        info_group = QGroupBox("Excel Raporu Oluştur")
        il = QVBoxLayout(info_group)

        info = QLabel(
            "Tüm hayvan ve hissedar bilgileri tek bir Excel dosyasına aktarılır.\n"
            "Ödeme durumuna göre hücreler renklendirilir:\n\n"
            "  🟢  Yeşil = Ödendi          🔴  Kırmızı = Ödenmedi\n\n"
            "Rapor: toplam fiyat/ağırlık, pay oranları ve hisse başı değerler."
        )
        info.setStyleSheet("font-size: 13px; line-height: 1.6; padding: 8px;")
        il.addWidget(info)

        self._btn_export = QPushButton("📊  Raporu Dışa Aktar")
        self._btn_export.setObjectName("btnExport")
        self._btn_export.setMinimumHeight(46)
        self._btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_export.clicked.connect(self._on_export)
        il.addWidget(self._btn_export)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFormat("İşleniyor…")
        self._progress.setVisible(False)
        self._progress.setMinimumHeight(28)
        il.addWidget(self._progress)

        self._export_status = QLabel("")
        self._export_status.setStyleSheet("color: #94A3B8; font-size: 12px; padding: 4px;")
        self._export_status.setWordWrap(True)
        il.addWidget(self._export_status)

        layout.addWidget(info_group)
        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )
        return tab

    def _on_export(self) -> None:
        records = self._ctrl.get_all_for_export()
        if not records:
            QMessageBox.information(self, "Bilgi", "Dışa aktarılacak kayıt bulunamadı.")
            return

        dest, _ = QFileDialog.getSaveFileName(
            self, "Rapor Kaydet",
            str(Path.home() / "kurban_raporu.xlsx"),
            "Excel Dosyası (*.xlsx)",
        )
        if not dest:
            return

        self._btn_export.setEnabled(False)
        self._progress.setVisible(True)
        self._export_status.setText("Rapor oluşturuluyor…")

        self._export_worker = ExportWorker(records, Path(dest))
        self._export_worker.finished.connect(self._on_export_finished)
        self._export_worker.start()

    def _on_export_finished(self, success: bool, message: str) -> None:
        self._progress.setVisible(False)
        self._btn_export.setEnabled(True)

        if success:
            self._export_status.setStyleSheet(
                "color: #22C55E; font-size: 12px; padding: 4px;"
            )
            self._export_status.setText(message)
            self._status_bar.showMessage("Dışa aktarma tamamlandı.", 5000)
            create_backup()
        else:
            self._export_status.setStyleSheet(
                "color: #EF4444; font-size: 12px; padding: 4px;"
            )
            self._export_status.setText(message)
            QMessageBox.critical(self, "Hata", message)

        self._export_worker = None
