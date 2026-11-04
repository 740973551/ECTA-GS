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


def test_review_workspace_parse_edit_approve_and_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    library = Library(tmp_path / "data")
    library.save_building(name="审核测试建筑")
    path = tmp_path / "证据.txt"
    path.write_text("柱高待考", encoding="utf-8")
    doc_id, _ = library.import_file(path, 1)
    window = MainWindow(library)
    workspace = window.review_workspace
    window.nav.setCurrentRow(3)
    assert workspace.file_list.count() == 1
    workspace.start_parse(False)
    assert window.busy
    window.pool.waitForDone(10000)
    app.processEvents()
    assert not window.busy
    assert workspace.segment_list.count() == 1
    workspace.segment_list.setCurrentRow(0)
    workspace.create_pending()
    assert workspace.claim is not None
    assert workspace.epistemic.currentData() == "hypothesis"
    workspace.content.setPlainText("柱高未知，需补测绘资料")
    workspace.category.setCurrentText("尺寸")
    workspace.reason.setText("原文没有明确数值，保留未知")
    workspace.save_claim("approved")
    assert library.claims(status="approved")[0][0].content == "柱高未知，需补测绘资料"
    assert "原文没有明确数值" in workspace.history.toPlainText()
    assert "已确认知识  1" in window.stats.text()
    assert "解析完成" in window.doc_table.item(0, 3).text()
    assert workspace.claim.version == 2
    workspace.start_parse(False)
    window.pool.waitForDone(10000)
    app.processEvents()
    assert len(library.extractions(doc_id)) == 2
    assert len(library.claims(status="approved")) == 1
    window.close()


def test_selecting_different_evidence_clears_previous_claim_editor(tmp_path):
    app = QApplication.instance() or QApplication([])
    library = Library(tmp_path / "data")
    bid = library.save_building(name="测试建筑")
    path = tmp_path / "资料.txt"
    path.write_text("第一条证据\n第二条证据", encoding="utf-8")
    doc_id, _ = library.import_file(path, bid)
    library.run_parse_task(library.queue_parsing([doc_id])[0])
    window = MainWindow(library)
    workspace = window.review_workspace
    workspace.create_pending()
    assert workspace.claim is not None
    workspace.segment_list.setCurrentRow(1)
    app.processEvents()
    assert workspace.claim is None
    assert not workspace.content.isEnabled()
    assert "第二条证据" in workspace.evidence.toPlainText()
    window.close()
