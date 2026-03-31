"""
main.py — V2.0 Application entry point.

Configures logging (file + console), initialises the database,
wires Repository → Controller → GUI, and starts the Qt event loop.

Path resolution uses ``database.APP_DIR`` which is Nuitka-safe.
"""

from __future__ import annotations

import logging
import sys

from database import APP_DIR, initialise_database

# ---------------------------------------------------------------------------
# Logging configuration  (must happen before any other import that logs)
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
    logger.info("===== Kurban Takip Sistemi V2.0 starting =====")

    # DB bootstrap
    initialise_database()

    # Late imports so logging is already configured
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    from controller import KurbanController
    from database import KurbanRepository
    from gui import MainWindow

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")

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
