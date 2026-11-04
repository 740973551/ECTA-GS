import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtCore import QPoint, QPointF
from ecta_gs.desktop import MainWindow
from ecta_gs.service import Library


def test_window_navigation_and_preview(tmp_path):
    app = QApplication.instance() or QApplication([])
    library = Library(tmp_path / "data")
    library.save_building(name="测试建筑")
    path = tmp_path / "证据.txt"
    path.write_text("测试原文", encoding="utf-8")
    library.import_file(path)
    window = MainWindow(library)
    window.show()
    app.processEvents()
    assert window.pages.count() == 7
    for row in range(7):
        window.nav.setCurrentRow(row)
        assert window.pages.currentIndex() == row
    window.nav.setCurrentRow(2)
    window.doc_table.selectRow(0)
    app.processEvents()
    assert "测试原文" in window.preview.toPlainText()
    assert "SHA-256" in window.metadata.toPlainText()
    window.assignment.setCurrentIndex(1)
    window.source_edit.setText("UI 来源")
    window.save_document()
    assert library.documents()[0][0].source_note == "UI 来源"
    assert library.documents()[0][0].building_id == 1
    window.close()


def test_drag_drop_multifile_uses_background_import(tmp_path):
    app = QApplication.instance() or QApplication([])
    library = Library(tmp_path / "data")
    window = MainWindow(library)
    paths = []
    for name in ("one.pdf", "two.dxf"):
        path = tmp_path / name
        path.write_bytes(b"test " + name.encode())
        paths.append(path)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    drag = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.dragEnterEvent(drag)
    assert drag.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    window.dropEvent(drop)
    assert drop.isAccepted()
    window.pool.waitForDone(10000)
    app.processEvents()
    assert not window.busy
    assert library.counts()["原始文件"] == 2
    assert window.doc_table.rowCount() == 2
    window.close()
