"""Three-column local parsing and human review workspace."""
import json
from PySide6.QtCore import Qt, QObject, Signal, QRunnable
from PySide6.QtGui import QImage, QPixmap, QImageReader
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextEdit, QSplitter, QListWidget, QScrollArea)
from ecta_gs.parsers import STATUS_LABELS, REVIEW_LABELS, EPISTEMIC_LABELS, CATEGORIES


def button(label, callback, layout):
    widget = QPushButton(label)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def readonly():
    widget = QTextEdit()
    widget.setReadOnly(True)
    return widget


class ParseSignals(QObject):
    progress = Signal(str)
    finished = Signal(object)


class ParseWorker(QRunnable):
    def __init__(self, library, task_ids):
        super().__init__()
        self.library, self.task_ids = library, task_ids
        self.signals = ParseSignals()

    def run(self):
        results = []
        for index, task_id in enumerate(self.task_ids, 1):
            self.signals.progress.emit(f"本地解析 {index}/{len(self.task_ids)}")
            try:
                results.append(self.library.run_parse_task(task_id))
            except Exception as exc:
                results.append(f"异常：{exc}")
        self.signals.finished.emit(results)


class ReviewWorkspace(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main, self.library = main, main.library
        self.documents = []
        self.segments = []
        self.claim_rows = []
        self.claim = None
        self.task_rows = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<h2>解析与人工审核</h2>"))
        help_text = QLabel("① 选择资料并解析 → ② 选中证据片段，加入待审核 → ③ 修改内容、选择事实状态并确认。\nAI：未配置。当前为本地解析与人工整理，未向外部服务发送资料。解析文本不自动成为已确认知识。")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        toolbar = QHBoxLayout()
        self.building_filter = QComboBox()
        toolbar.addWidget(self.building_filter)
        button("刷新", self.refresh, toolbar)
        self.parse_one = button("解析所选 / 重新解析", lambda: self.start_parse(False), toolbar)
        self.parse_batch = button("批量解析待处理资料", lambda: self.start_parse(True), toolbar)
        layout.addLayout(toolbar)
        self.building_filter.currentIndexChanged.connect(self.refresh_documents)
        splitter = QSplitter()
        left, center, right = QWidget(), QWidget(), QWidget()
        a, b, c = QVBoxLayout(left), QVBoxLayout(center), QVBoxLayout(right)
        a.addWidget(QLabel("原始资料"))
        self.file_list = QListWidget()
        self.file_list.setMinimumWidth(200)
        self.file_list.setMaximumHeight(180)
        a.addWidget(self.file_list)
        self.file_info = readonly()
        self.file_info.setMaximumHeight(135)
        a.addWidget(self.file_info)
        self.source_text = readonly()
        a.addWidget(self.source_text)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidget(self.image)
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.setMaximumHeight(300)
        self.image_scroll.hide()
        a.addWidget(self.image_scroll)
        b.addWidget(QLabel("解析证据 · 每次运行独立保留"))
        self.task_filter = QComboBox()
        b.addWidget(self.task_filter)
        self.segment_list = QListWidget()
        self.segment_list.setMinimumWidth(210)
        b.addWidget(self.segment_list, 1)
        self.evidence = readonly()
        b.addWidget(self.evidence, 1)
        button("选中片段 → 加入待审核", self.create_pending, b)
        b.addWidget(QLabel("此资料的人工知识条目（包括历史状态）"))
        self.claim_list = QListWidget()
        b.addWidget(self.claim_list, 1)
        c.addWidget(QLabel("人工整理与审核"))
        self.claim_label = QLabel("请选择知识条目，或从证据片段新建；编辑后点击按钮保存")
        self.claim_label.setWordWrap(True)
        c.addWidget(self.claim_label)
        self.category = QComboBox()
        self.category.addItems(CATEGORIES)
        c.addWidget(self.category)
        self.epistemic = QComboBox()
        for key, label in EPISTEMIC_LABELS.items():
            self.epistemic.addItem(label, key)
        self.epistemic.setCurrentIndex(self.epistemic.findData("hypothesis"))
        c.addWidget(self.epistemic)
        self.content = QTextEdit()
        self.content.setPlaceholderText("人工编辑知识内容；尺寸请写明数值、单位、测量位置及不确定性")
        c.addWidget(self.content, 2)
        self.reviewer = QLineEdit("本地研究者")
        self.reviewer.setPlaceholderText("修改者（本地署名，非身份认证）")
        c.addWidget(self.reviewer)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("必填：修改 / 审核理由；推导请写公式与输入，类比请写对象与依据")
        c.addWidget(self.reason)
        actions = QHBoxLayout()
        self.review_buttons = [
            button("保存待审核", lambda: self.save_claim("pending_review"), actions),
            button("确认", lambda: self.save_claim("approved"), actions),
            button("否决", lambda: self.save_claim("rejected"), actions),
            button("争议", lambda: self.save_claim("disputed"), actions),
        ]
        c.addLayout(actions)
        c.addWidget(QLabel("版本历史 · 原文、前后值、理由与时间"))
        self.history = readonly()
        c.addWidget(self.history, 1)
        for widget in (left, center, right):
            splitter.addWidget(widget)
        splitter.setSizes([300, 350, 400])
        layout.addWidget(splitter)
        self.file_list.currentRowChanged.connect(self.show_document)
        self.task_filter.currentIndexChanged.connect(self.load_segments)
        self.segment_list.currentRowChanged.connect(self.show_segment)
        self.claim_list.currentRowChanged.connect(self.show_claim)
        self.set_editor_enabled(False)

    def set_editor_enabled(self, enabled):
        for widget in (self.category, self.epistemic, self.content, self.reason, *self.review_buttons):
            widget.setEnabled(enabled)

    def refresh(self):
        previous = self.building_filter.currentData() if self.building_filter.count() else "all"
        self.building_filter.blockSignals(True)
        self.building_filter.clear()
        self.building_filter.addItem("全部建筑资料", "all")
        self.building_filter.addItem("未分配资料箱", None)
        for building in self.library.buildings():
            self.building_filter.addItem(building.name, building.id)
        self.building_filter.setCurrentIndex(max(0, self.building_filter.findData(previous)))
        self.building_filter.blockSignals(False)
        self.refresh_documents()

    def selected_document(self):
        index = self.file_list.currentRow()
        return self.documents[index] if 0 <= index < len(self.documents) else None

    def refresh_documents(self):
        selected = self.selected_document()
        selected_id = selected[0].id if selected else None
        self.documents = self.library.documents(self.building_filter.currentData())
        names = {b.id: b.name for b in self.library.buildings()}
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for doc, _ in self.documents:
            self.file_list.addItem(f"{doc.filename}\n{names.get(doc.building_id, '未分配')} · {STATUS_LABELS.get(doc.status, doc.status)}")
        row = next((i for i, (doc, _) in enumerate(self.documents) if doc.id == selected_id), 0)
        self.file_list.setCurrentRow(row if self.documents else -1)
        self.file_list.blockSignals(False)
        self.show_document()

    def show_document(self, *_):
        selected = self.selected_document()
        self.file_info.clear()
        self.source_text.clear()
        self.image.clear()
        self.image_scroll.hide()
        self.task_filter.blockSignals(True)
        self.task_filter.clear()
        self.task_rows = []
        if selected:
            doc, asset = selected
            self.file_info.setPlainText(f"{doc.filename}\nSHA-256：{asset.sha256}\n{STATUS_LABELS.get(doc.status, doc.status)}\n{doc.error}\n来源：{doc.source_note}")
            self.source_text.setPlainText("选中证据后显示原文摘录。PDF 显示对应页面，图片显示全幅；Word/Excel 等摘录不模拟原始版式。")
            self.task_rows = self.library.parse_tasks(doc.id)
            for task in self.task_rows:
                self.task_filter.addItem(f"运行 #{task.id} · {STATUS_LABELS.get(task.status, task.status)} · {task.created_at}", task.id)
            self.task_filter.setToolTip("\n".join(f"#{t.id} {t.message}" for t in self.task_rows))
        self.task_filter.blockSignals(False)
        self.load_segments()
        self.load_claims()

    def load_segments(self, *_):
        self.claim_list.setCurrentRow(-1)
        selected = self.selected_document()
        self.segments = self.library.extractions(selected[0].id, self.task_filter.currentData()) if selected and self.task_filter.currentData() else []
        self.segment_list.blockSignals(True)
        self.segment_list.clear()
        for segment in self.segments:
            self.segment_list.addItem(f"{segment.locator}\n{segment.text[:80].replace(chr(10), ' ')}")
        self.segment_list.blockSignals(False)
        self.evidence.clear()
        if self.task_filter.currentIndex() >= 0:
            task = self.task_rows[self.task_filter.currentIndex()]
            self.evidence.setPlainText(f"{task.parser}\n{task.message}")
        if self.segments:
            self.segment_list.setCurrentRow(0)

    def show_evidence(self, segment):
        selected = self.selected_document()
        self.evidence.setPlainText(f"证据定位：{segment.locator}\n解析器：{segment.parser}\n{segment.details}\n\n{segment.text}")
        self.source_text.setPlainText(segment.text or "此处未提取文本；请人工查看原件并在审核理由中说明依据。")
        self.image_scroll.hide()
        if not selected:
            return
        doc, asset = selected
        path = self.library.root / asset.relative_path
        try:
            details = json.loads(segment.details)
            if doc.extension == ".pdf" and details.get("page"):
                import pymupdf
                with pymupdf.open(path) as pdf:
                    page = pdf[details["page"] - 1]
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(min(1.2, 900 / max(page.rect.width, page.rect.height)), min(1.2, 900 / max(page.rect.width, page.rect.height))), alpha=False)
                    image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
            elif doc.extension in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                reader = QImageReader(str(path))
                size = reader.size()
                if size.isValid():
                    reader.setScaledSize(size.scaled(600, 600, Qt.KeepAspectRatio))
                image = reader.read()
                if image.isNull():
                    raise ValueError(reader.errorString())
            else:
                return
            self.image.setPixmap(QPixmap.fromImage(image))
            self.image_scroll.show()
        except Exception as exc:
            self.source_text.append(f"\n原件版式预览失败：{exc}")

    def show_segment(self, row):
        if 0 <= row < len(self.segments):
            self.claim_list.setCurrentRow(-1)
            self.show_evidence(self.segments[row])

    def load_claims(self, preferred_id=None):
        selected = self.selected_document()
        self.claim_rows = self.library.claims(document_id=selected[0].id) if selected else []
        self.claim_list.blockSignals(True)
        self.claim_list.clear()
        for claim, _, _ in self.claim_rows:
            self.claim_list.addItem(f"#{claim.id} · {REVIEW_LABELS[claim.status]} · {claim.category}\n{claim.content[:80].replace(chr(10), ' ')}")
        index = next((i for i, (claim, _, _) in enumerate(self.claim_rows) if claim.id == preferred_id), -1)
        self.claim_list.setCurrentRow(index)
        self.claim_list.blockSignals(False)
        self.show_claim(index)

    def show_claim(self, row):
        self.claim = None
        self.set_editor_enabled(False)
        self.content.clear()
        self.reason.clear()
        self.history.clear()
        self.claim_label.setText("请选择知识条目，或从证据片段新建；编辑后点击按钮保存")
        if 0 <= row < len(self.claim_rows):
            claim, extraction, doc = self.claim_rows[row]
            self.claim = claim
            self.set_editor_enabled(True)
            self.category.setCurrentText(claim.category)
            self.epistemic.setCurrentIndex(self.epistemic.findData(claim.epistemic))
            self.content.setPlainText(claim.content)
            self.claim_label.setText(f"条目 #{claim.id} · 版本 {claim.version} · {REVIEW_LABELS[claim.status]}\n{doc.filename} · {extraction.locator}")
            self.show_evidence(extraction)
            def describe(raw):
                value = json.loads(raw)
                if not value:
                    return "尚无条目"
                return (f"版本 {value['version']} · {value['category']} · "
                        f"{EPISTEMIC_LABELS[value['epistemic']]} · {REVIEW_LABELS[value['status']]}\n"
                        + value['content'])
            history = []
            for review in self.library.review_history(claim.id):
                history.append(f"{review.created_at}（UTC） · {review.reviewer}\n理由：{review.reason}\n修改前：{describe(review.before)}\n修改后：{describe(review.after)}")
            self.history.setPlainText("\n\n".join(history))

    def create_pending(self):
        row = self.segment_list.currentRow()
        if not 0 <= row < len(self.segments):
            return self.main.error("请先解析资料，并选择一个证据片段")
        try:
            claim_id = self.library.create_pending_claim(self.segments[row].id, reviewer=self.reviewer.text())
            self.load_claims(claim_id)
            self.main.refresh_stats()
        except Exception as exc:
            self.main.error(exc)

    def save_claim(self, status):
        if self.claim is None:
            return
        try:
            claim_id = self.claim.id
            self.library.review_claim(claim_id, self.claim.version, content=self.content.toPlainText(),
                category=self.category.currentText(), epistemic=self.epistemic.currentData(), status=status,
                reviewer=self.reviewer.text(), reason=self.reason.text())
            self.load_claims(claim_id)
            self.main.refresh_stats()
            self.main.statusBar().showMessage("审核已保存；原始解析结果和所有版本保留")
        except Exception as exc:
            self.main.error(exc)

    def start_parse(self, batch):
        if self.main.busy:
            return self.main.error("当前有导入或解析任务，请等待完成")
        selected = self.selected_document()
        ids = [doc.id for doc, _ in self.documents if doc.status in ("saved_unparsed", "failed", "interrupted")] if batch else ([selected[0].id] if selected else [])
        if not ids:
            return self.main.error("没有可解析资料；请先导入文件或选择一份资料")
        try:
            task_ids = self.library.queue_parsing(ids)
            self.main.busy = True
            self.parse_one.setEnabled(False)
            self.parse_batch.setEnabled(False)
            self.refresh_documents()
            self.worker = ParseWorker(self.library, task_ids)
            self.worker.signals.progress.connect(self.main.statusBar().showMessage)
            self.worker.signals.finished.connect(self.parse_done)
            self.main.pool.start(self.worker)
        except Exception as exc:
            self.main.busy = False
            self.parse_one.setEnabled(True)
            self.parse_batch.setEnabled(True)
            self.main.error(exc)

    def parse_done(self, results):
        self.main.busy = False
        self.parse_one.setEnabled(True)
        self.parse_batch.setEnabled(True)
        self.main.refresh()
        summary = "；".join(f"{STATUS_LABELS.get(status, status)} {results.count(status)}" for status in dict.fromkeys(results))
        self.main.statusBar().showMessage("解析批次完成：" + summary + "；失败详情见所选资料的运行记录")
