from pathlib import Path
import logging
from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QStackedWidget,
    QLabel, QPushButton, QLineEdit, QFormLayout, QDialog, QDialogButtonBox,
    QCheckBox, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit,
    QFileDialog, QMessageBox, QSplitter, QAbstractItemView,
)
from ecta_gs.service import expand_paths
from ecta_gs.parsers import STATUS_LABELS
from ecta_gs.review_desktop import ReviewWorkspace

log = logging.getLogger(__name__)


def button(text, action, layout):
    widget = QPushButton(text)
    widget.clicked.connect(action)
    layout.addWidget(widget)
    return widget


class BuildingDialog(QDialog):
    def __init__(self, parent, building=None):
        super().__init__(parent)
        self.setWindowTitle("编辑建筑档案" if building else "新建建筑档案")
        self.resize(520, 420)
        form = QFormLayout(self)
        self.fields = {}
        for key, label in (("name", "建筑名称 *"), ("aliases", "别名"), ("location", "地点"), ("period", "时期（可未知）"), ("building_type", "建筑类型"), ("dating_basis", "年代判断与依据")):
            edit = QLineEdit(getattr(building, key, ""))
            self.fields[key] = edit
            form.addRow(label, edit)
        self.archived = QCheckBox("归档（保留所有资料）")
        self.archived.setChecked(bool(building and building.archived))
        form.addRow(self.archived)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        form.addRow(controls)

    def values(self):
        return {**{k: w.text() for k, w in self.fields.items()}, "archived": self.archived.isChecked()}


class ImportSignals(QObject):
    progress = Signal(str)
    done = Signal(int, int, object)


class ImportWorker(QRunnable):
    def __init__(self, library, paths, building, source):
        super().__init__()
        self.library, self.paths, self.building, self.source = library, paths, building, source
        self.signals = ImportSignals()

    def run(self):
        imported = duplicates = 0
        errors = []
        try:
            for path in expand_paths(self.paths, self.library.root):
                self.signals.progress.emit(f"正在保存：{path.name}")
                try:
                    _, duplicate = self.library.import_file(path, self.building, self.source)
                    duplicates += int(duplicate)
                    imported += int(not duplicate)
                except Exception as exc:
                    log.exception("Import failed: %s", path)
                    errors.append(f"{path.name}: {exc}")
        except Exception as exc:
            errors.append(str(exc))
        self.signals.done.emit(imported, duplicates, errors)


class MainWindow(QMainWindow):
    def __init__(self, library):
        super().__init__()
        self.library = library
        self.busy = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle("ECTA-GS · 建筑知识资料工作台 | Alpha 0.1.1 · 本地解析与人工审核")
        self.resize(1280, 820)
        self.setAcceptDrops(True)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        self.nav = QListWidget()
        self.nav.addItems(["项目概览", "建筑档案", "资料投喂", "解析与审核", "构件知识库", "研究助手", "设置与备份"])
        self.nav.setFixedWidth(180)
        layout.addWidget(self.nav)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.build_overview()
        self.build_buildings()
        self.build_documents()
        self.review_workspace = ReviewWorkspace(self)
        self.pages.addWidget(self.review_workspace)
        for title in ("构件知识库", "AI 研究助手"):
            page = QWidget()
            box = QVBoxLayout(page)
            box.addWidget(QLabel(f"<h2>{title}</h2>"))
            message = "本模块将在后续增量实现。已可在“解析与审核”页本地解析文件、人工整理并审核知识。\nAI 服务：未配置。当前没有外部资料上传。"
            label = QLabel(message)
            label.setWordWrap(True)
            box.addWidget(label)
            box.addStretch()
            self.pages.addWidget(page)
        self.build_settings()
        self.nav.setCurrentRow(0)
        self.refresh()
        self.statusBar().showMessage("本地资料库已就绪 · 可拖入文件或文件夹")
        self.setStyleSheet("QMainWindow {background:#f4f5f7;} QListWidget {background:#21352f;color:#f1f5ef;border:0;padding:12px;font-size:15px;} QListWidget::item {padding:14px 4px;} QListWidget::item:selected {background:#3e6456;} QPushButton {padding:7px 12px;} QLineEdit,QTextEdit,QComboBox {padding:5px;} QTableWidget {background:white;gridline-color:#e0e4e2;} QLabel {color:#23382f;} ")

    def build_overview(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(QLabel("<h1>ECTA-GS</h1><h3>先认识每栋建筑，再比较建筑</h3>"))
        self.stats = QLabel()
        self.stats.setStyleSheet("font-size:17px;padding:16px;background:white;")
        self.stats.setWordWrap(True)
        box.addWidget(self.stats)
        box.addWidget(QLabel("本轮：本地资料解析与人工审核 · 无外部资料上传\n解析结果与审核知识分开保存，历史版本持续保留。"))
        box.addWidget(QLabel("<h3>最近导入资料</h3>"))
        self.recent = QTextEdit()
        self.recent.setReadOnly(True)
        box.addWidget(self.recent)
        self.recent_buildings = QLabel()
        box.addWidget(self.recent_buildings)
        button("开始投喂资料", lambda: self.nav.setCurrentRow(2), box)
        self.pages.addWidget(page)

    def build_buildings(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(QLabel("<h2>建筑档案</h2>"))
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称、别名、地点、时期")
        self.search.textChanged.connect(self.refresh_buildings)
        bar.addWidget(self.search)
        button("新建建筑", lambda: self.edit_building(False), bar)
        button("编辑 / 归档", lambda: self.edit_building(True), bar)
        button("添加营建阶段", self.add_phase, bar)
        box.addLayout(bar)
        self.building_table = self.table(["名称", "别名", "地点", "时期", "类型", "归档", "年代依据"])
        self.building_table.itemSelectionChanged.connect(self.show_phases)
        box.addWidget(self.building_table)
        self.phase_text = QTextEdit()
        self.phase_text.setReadOnly(True)
        self.phase_text.setMaximumHeight(180)
        box.addWidget(self.phase_text)
        self.pages.addWidget(page)

    def build_documents(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(QLabel("<h2>资料投喂</h2>将文件或文件夹拖入窗口。原件只复制，不修改。"))
        bar = QHBoxLayout()
        bar.addWidget(QLabel("导入归属"))
        self.import_building = QComboBox()
        bar.addWidget(self.import_building)
        self.import_source = QLineEdit()
        self.import_source.setPlaceholderText("本批来源说明（可选）")
        bar.addWidget(self.import_source)
        self.import_buttons = [button("选择文件", self.choose_files, bar), button("选择文件夹", self.choose_folder, bar)]
        box.addLayout(bar)
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("查看归属"))
        self.doc_filter = QComboBox()
        self.doc_filter.currentIndexChanged.connect(self.refresh_documents)
        filter_bar.addWidget(self.doc_filter)
        filter_bar.addStretch()
        box.addLayout(filter_bar)
        splitter = QSplitter()
        self.doc_table = self.table(["文件名", "所属建筑", "大小", "状态", "导入时间"])
        self.doc_table.itemSelectionChanged.connect(self.show_document)
        splitter.addWidget(self.doc_table)
        right = QWidget()
        form = QVBoxLayout(right)
        self.metadata = QTextEdit()
        self.metadata.setReadOnly(True)
        self.metadata.setMaximumHeight(150)
        form.addWidget(self.metadata)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        form.addWidget(self.preview)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        form.addWidget(self.image)
        self.assignment = QComboBox()
        form.addWidget(self.assignment)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("来源说明")
        form.addWidget(self.source_edit)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("备注")
        form.addWidget(self.notes_edit)
        button("保存归属与备注", self.save_document, form)
        splitter.addWidget(right)
        splitter.setSizes([680, 420])
        box.addWidget(splitter)
        self.pages.addWidget(page)

    def build_settings(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(QLabel("<h2>设置与备份</h2>"))
        label = QLabel(f"本地数据目录：\n{self.library.root}\n\nAI：未配置\n更换数据目录：启动前设置 ECTA_GS_DATA_DIR 环境变量。\n\n完整备份：关闭程序后，复制整个数据目录。\n仅数据库备份不包含原件，不能替代完整备份。\n日志不记录原文内容或密钥。")
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setWordWrap(True)
        box.addWidget(label)
        button("备份数据库快照", self.backup, box)
        button("打开数据目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library.root))), box)
        box.addStretch()
        self.pages.addWidget(page)

    @staticmethod
    def table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def error(self, exc):
        QMessageBox.warning(self, "未完成操作", str(exc))

    def refresh(self):
        self.all_buildings = self.library.buildings()
        self.names = {b.id: b.name for b in self.all_buildings}
        self.refresh_buildings()
        for combo, all_option in ((self.import_building, False), (self.assignment, False), (self.doc_filter, True)):
            previous = combo.currentData() if combo.count() or not all_option else "all"
            combo.blockSignals(True)
            combo.clear()
            if all_option:
                combo.addItem("全部资料", "all")
            combo.addItem("未分配资料箱", None)
            for b in self.all_buildings:
                combo.addItem(b.name + (" [已归档]" if b.archived else ""), b.id)
            index = combo.findData(previous)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        self.refresh_documents()
        self.refresh_stats()
        self.review_workspace.refresh()
        self.recent.setPlainText("\n".join(f"{d.imported_at}  {d.filename}  ·  {self.names.get(d.building_id, '未分配')}" for d, _ in self.library.documents()[:12]) or "尚无资料。可以开始导入。")
        self.recent_buildings.setText("最近更新建筑：" + ("、".join(b.name for b in self.all_buildings[:5]) or "暂无"))

    def refresh_stats(self):
        self.stats.setText("    |    ".join(f"{k}  {v}" for k, v in self.library.counts().items()))

    def refresh_buildings(self):
        self.building_rows = self.library.buildings(self.search.text())
        self.building_table.setRowCount(0)
        for b in self.building_rows:
            row = self.building_table.rowCount()
            self.building_table.insertRow(row)
            for col, value in enumerate((b.name, b.aliases, b.location, b.period, b.building_type, "是" if b.archived else "否", b.dating_basis)):
                self.building_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.show_phases()

    def selected_building(self):
        row = self.building_table.currentRow()
        return self.building_rows[row] if 0 <= row < len(self.building_rows) else None

    def edit_building(self, existing):
        building = self.selected_building() if existing else None
        if existing and building is None:
            return self.error("请先选择建筑")
        dialog = BuildingDialog(self, building)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.library.save_building(building.id if building else None, **dialog.values())
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def show_phases(self):
        building = self.selected_building()
        phases = self.library.phases(building.id) if building else []
        self.phase_text.setPlainText("营建阶段（不预填历史结论）\n" + "\n".join(f"{p.kind} | {p.date_statement} | 依据：{p.evidence}" for p in phases))

    def add_phase(self):
        building = self.selected_building()
        if not building:
            return self.error("请先选择建筑")
        dialog = QDialog(self)
        dialog.setWindowTitle("添加营建阶段 · " + building.name)
        form = QFormLayout(dialog)
        kind = QComboBox()
        kind.addItems(["初建", "重建", "修缮", "替换", "未知"])
        date, evidence = QLineEdit(), QLineEdit()
        form.addRow("阶段", kind)
        form.addRow("年代陈述", date)
        form.addRow("依据（可未知）", evidence)
        controls = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        controls.accepted.connect(dialog.accept)
        controls.rejected.connect(dialog.reject)
        form.addRow(controls)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.library.add_phase(building.id, kind.currentText(), date.text(), evidence.text())
                self.show_phases()
            except Exception as exc:
                self.error(exc)

    def refresh_documents(self):
        self.doc_rows = self.library.documents(self.doc_filter.currentData())
        self.doc_table.setRowCount(0)
        for doc, asset in self.doc_rows:
            row = self.doc_table.rowCount()
            self.doc_table.insertRow(row)
            for col, value in enumerate((doc.filename, self.names.get(doc.building_id, "未分配"), f"{asset.size:,} B", STATUS_LABELS.get(doc.status, doc.status), doc.imported_at)):
                self.doc_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.show_document()

    def selected_document(self):
        row = self.doc_table.currentRow()
        return self.doc_rows[row] if 0 <= row < len(self.doc_rows) else None

    def show_document(self):
        selected = self.selected_document()
        self.metadata.clear()
        self.preview.clear()
        self.image.clear()
        self.source_edit.clear()
        self.notes_edit.clear()
        if not selected:
            return
        doc, asset = selected
        self.metadata.setPlainText(f"文件：{doc.filename}\n扩展名：{doc.extension}\nSHA-256：{asset.sha256}\n入库时间（UTC）：{doc.imported_at}\n状态：{STATUS_LABELS.get(doc.status, doc.status)}\n解析提示：{doc.error or '无'}\n原件位置：{asset.relative_path}")
        self.assignment.setCurrentIndex(self.assignment.findData(doc.building_id))
        self.source_edit.setText(doc.source_note)
        self.notes_edit.setText(doc.notes)
        path = self.library.root / asset.relative_path
        try:
            if doc.extension in (".txt", ".md", ".csv", ".json"):
                with path.open("rb") as stream:
                    raw = stream.read(65536)
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = raw.decode("gb18030", errors="replace")
                self.preview.setPlainText("原文预览（最多 64 KiB，未解析；非 UTF-8 按 GB18030 尝试）\n\n" + text)
            elif doc.extension in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
                from PySide6.QtGui import QImageReader
                reader = QImageReader(str(path))
                size = reader.size()
                if size.isValid():
                    reader.setScaledSize(size.scaled(420, 300, Qt.KeepAspectRatio))
                image = reader.read()
                if image.isNull():
                    raise ValueError("图片预览失败：" + reader.errorString())
                self.image.setPixmap(QPixmap.fromImage(image))
                self.preview.setPlainText("图片人工预览；OCR 尚未启用。")
            else:
                self.preview.setPlainText("原件已保存。请到“解析与审核”页运行本地解析并查看证据。")
        except Exception as exc:
            self.preview.setPlainText(f"预览失败：{exc}")

    def save_document(self):
        selected = self.selected_document()
        if selected:
            try:
                self.library.update_document(selected[0].id, self.assignment.currentData(), self.source_edit.text(), self.notes_edit.text())
                self.refresh()
            except Exception as exc:
                self.error(exc)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择资料（所有格式均保存原件）")
        if paths:
            self.start_import(paths)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择资料文件夹（递归导入，跳过符号链接）")
        if path:
            self.start_import([path])

    def start_import(self, paths):
        if self.busy:
            return self.error("当前有导入或解析任务，请等待完成后再导入")
        self.busy = True
        self.nav.setCurrentRow(2)
        for widget in self.import_buttons:
            widget.setEnabled(False)
        worker = ImportWorker(self.library, paths, self.import_building.currentData(), self.import_source.text())
        worker.signals.progress.connect(self.statusBar().showMessage)
        worker.signals.done.connect(self.import_done)
        self.worker = worker
        self.pool.start(worker)

    def import_done(self, imported, duplicates, errors):
        self.busy = False
        for widget in self.import_buttons:
            widget.setEnabled(True)
        self.refresh()
        message = f"导入完成：新增 {imported}，重复 {duplicates}，失败 {len(errors)}"
        self.statusBar().showMessage(message)
        if errors:
            self.error(message + "\n\n" + "\n".join(errors[:15]) + "\n更多错误见日志。")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.start_import([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
        event.acceptProposedAction()

    def backup(self):
        try:
            path = self.library.backup_database()
            QMessageBox.information(self, "数据库快照已保存", str(path) + "\n完整备份还须复制 library 原件目录。")
        except Exception as exc:
            self.error(exc)

    def closeEvent(self, event):
        if self.busy:
            self.error("文件正在导入或解析，请等待当前批次完成后关闭。")
            event.ignore()
        else:
            self.pool.waitForDone()
            self.library.close()
            event.accept()
