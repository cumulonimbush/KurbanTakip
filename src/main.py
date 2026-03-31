"""
main.py — V2.1 Application entry point.

V2.1 changes
-------------
* Loads ``style.qss`` from disk (Nuitka-safe ``APP_DIR``) and applies it
  globally via ``app.setStyleSheet()``.
"""

from __future__ import annotations

import logging
import sys

from database import APP_DIR, initialise_database

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

LOG_FILE = APP_DIR / "app.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("===== Kurban Takip Sistemi V2.1 starting =====")

    initialise_database()

    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    from controller import KurbanController
    from database import KurbanRepository
    from gui import MainWindow

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")

    # ── Load external QSS stylesheet ───────────────────────────────────
    qss_path = APP_DIR / "style.qss"
    if qss_path.exists():
        qss_text = qss_path.read_text(encoding="utf-8")
        app.setStyleSheet(qss_text)
        logger.info("Loaded stylesheet from %s", qss_path)
    else:
        logger.warning("style.qss not found at %s — using defaults", qss_path)

    repo = KurbanRepository()
    controller = KurbanController(repo)

    window = MainWindow(controller)
    window.show()

    logger.info("Main window displayed — entering event loop")
    exit_code = app.exec()
    logger.info("Application exiting with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
