import logging
from logging.handlers import RotatingFileHandler
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from ecta_gs.service import Library
from ecta_gs.desktop import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ECTA-GS")
    try:
        library = Library()
        handler = RotatingFileHandler(library.root / "logs/ecta.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s %(message)s")
        window = MainWindow(library)
        window.show()
    except Exception as exc:
        QMessageBox.critical(None, "ECTA-GS 启动失败", f"{exc}\n请检查数据目录权限；不要删除原件或数据库。")
        return 1
    return app.exec()
