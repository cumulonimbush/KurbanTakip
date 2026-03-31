"""
backup.py — V2.0 Timestamped database backup utility.

Creates ``Backups/backup_YYYYMMDD_HHMM.db`` inside the application dir.
Called:
  * On application close (``QMainWindow.closeEvent``)
  * After every successful Excel export
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from database import APP_DIR, DB_FILE

logger = logging.getLogger(__name__)

BACKUP_DIR = APP_DIR / "Backups"


def create_backup(db_path: Path = DB_FILE) -> Path | None:
    """Copy *db_path* to a timestamped file in ``Backups/``.

    Returns the backup path on success, or ``None`` on failure.
    """
    if not db_path.exists():
        logger.warning("Backup skipped — DB file not found: %s", db_path)
        return None

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        dest = BACKUP_DIR / f"backup_{stamp}.db"

        # Avoid overwriting if called twice in the same minute
        counter = 1
        while dest.exists():
            dest = BACKUP_DIR / f"backup_{stamp}_{counter}.db"
            counter += 1

        shutil.copy2(str(db_path), str(dest))
        logger.info("Database backed up → %s", dest)
        return dest
    except Exception:
        logger.exception("Backup failed")
        return None
