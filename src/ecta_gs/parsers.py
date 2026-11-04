"""Local, evidence-preserving parsers. No semantic/AI inference or uploads."""
import csv
from dataclasses import dataclass, field
from importlib.metadata import version
import io
import json
from pathlib import Path
import zipfile


STATUS_LABELS = {
    "saved_unparsed": "已保存，尚未解析", "queued": "等待解析", "running": "正在解析",
    "parsed": "解析完成", "failed": "解析失败", "unsupported": "已保存，格式尚不支持解析",
    "needs_ocr": "待 OCR（已有文本保留）", "interrupted": "解析中断，可重试",
}
REVIEW_LABELS = {"pending_review": "待审核", "approved": "已确认", "rejected": "已否决", "disputed": "有争议", "superseded": "已取代"}
EPISTEMIC_LABELS = {"measured": "实测 / 直接观察", "derived": "公式推导", "analogy": "类比", "hypothesis": "假设 / 待验证"}
CATEGORIES = ["建筑信息", "构件", "尺寸", "术语", "结构关系", "材料工艺", "研究问题", "其他"]


@dataclass(frozen=True)
class Segment:
    locator: str
    text: str
    details: dict = field(default_factory=dict)


@dataclass
class Parsed:
    parser: str
    segments: list[Segment]
    status: str = "parsed"
    message: str = ""


def decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        from charset_normalizer import from_bytes
        match = from_bytes(raw).best()
        if match is None:
            raise ValueError("无法可靠识别文本编码，请先转换为 UTF-8")
        return str(match), f"{match.encoding}（自动估计，请核对）"


def parse(path, extension):
    path = Path(path)
    ext = extension.lower()
    supported = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md", ".json", ".dxf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    if ext not in supported:
        return Parsed("registry/1", [], "unsupported", "原件已保留；DOC/XLS 请转换为 DOCX/XLSX，DWG 请转换为 DXF；其他格式尚未提供适配器。")
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("此版本单次解析上限为 100 MiB；原件已保留，请拆分资料后导入")
    if ext in {".docx", ".xlsx"}:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist()) > 256 * 1024 * 1024:
                raise ValueError("解压后资料过大，已停止解析，原件保留")
    segments = []
    text_size = 0

    def add(locator, text, **details):
        nonlocal text_size
        text = str(text)
        text_size += len(text)
        if len(segments) >= 20000 or text_size > 5_000_000:
            raise ValueError("解析结果超过本版本上限（2 万片段 / 500 万字符），请拆分资料；未提交不完整结果")
        segments.append(Segment(locator, text, details))

    if ext in {".txt", ".md", ".csv", ".json"}:
        content, encoding = decode(path.read_bytes())
        if ext == ".json":
            data = json.loads(content)
            add("JSON 根节点 $（文件级定位）", json.dumps(data, ensure_ascii=False, indent=2), encoding=encoding, precision="file")
        elif ext == ".csv":
            try:
                dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(io.StringIO(content, newline=""), dialect)
            previous_line = 0
            for row_index, row in enumerate(reader, 1):
                add(f"CSV 记录 {row_index} · 行 {previous_line + 1}–{reader.line_num}", json.dumps(row, ensure_ascii=False), encoding=encoding, row=row_index, delimiter=dialect.delimiter, header_candidate=row_index == 1)
                previous_line = reader.line_num
        else:
            for line, value in enumerate(content.splitlines(), 1):
                if value.strip():
                    add(f"行 {line}", value, line=line, encoding=encoding)
        result = Parsed("python-text/2", segments)
    elif ext == ".pdf":
        import pymupdf
        warnings = []
        scans = 0
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ValueError("PDF 已加密，请先提供可读取副本")
            for i, page in enumerate(pdf, 1):
                text = page.get_text("text", sort=True)
                if not text.strip():
                    scans += 1
                add(f"PDF 第 {i} 页", text, page=i, needs_ocr=not text.strip())
                try:
                    for j, table in enumerate(page.find_tables().tables, 1):
                        add(f"PDF 第 {i} 页 · 表 {j}", json.dumps(table.extract(), ensure_ascii=False), page=i, table=j, bbox=list(table.bbox))
                except Exception:
                    warnings.append(f"第 {i} 页表格识别失败，已保留文本；请人工核对")
        result = Parsed(f"PyMuPDF/{version('PyMuPDF')}", segments, "needs_ocr" if scans else "parsed", "；".join(([f"{scans} 页无可提取文本，待 OCR"] if scans else []) + warnings))
    elif ext == ".docx":
        from docx import Document
        doc = Document(path)
        for i, paragraph in enumerate(doc.paragraphs, 1):
            if paragraph.text.strip():
                add(f"Word 段落 {i}", paragraph.text, paragraph=i, style=paragraph.style.name if paragraph.style else "")
        for i, table in enumerate(doc.tables, 1):
            for row_index, row in enumerate(table.rows, 1):
                for col_index, cell in enumerate(row.cells, 1):
                    if cell.text.strip():
                        add(f"Word 表 {i} · 行 {row_index} 列 {col_index}", cell.text, table=i, row=row_index, column=col_index)
        result = Parsed(f"python-docx/{version('python-docx')}", segments, message="页码不由 DOCX 段落推断；页眉、页脚、文本框和嵌套表尚未提取。")
    elif ext == ".xlsx":
        from openpyxl import load_workbook
        with path.open("rb") as formula_stream, path.open("rb") as value_stream:
            formulas = load_workbook(formula_stream, read_only=True, data_only=False)
            try:
                values = load_workbook(value_stream, read_only=True, data_only=True)
                try:
                    for sheet in formulas:
                        if sheet.max_row and sheet.max_column and sheet.max_row * sheet.max_column > 200000:
                            raise ValueError("工作表范围超过 20 万单元格，请清理空白格式或拆分后重试")
                        value_rows = values[sheet.title].iter_rows()
                        for row, cached_row in zip(sheet.iter_rows(), value_rows):
                            for cell, cached in zip(row, cached_row):
                                if cell.value is not None:
                                    value = cached.value if cell.data_type == "f" else cell.value
                                    add(f"工作表 {sheet.title} · {cell.coordinate}", str(value) if value is not None else "[公式无缓存值]", sheet=sheet.title, cell=cell.coordinate, formula=str(cell.value) if cell.data_type == "f" else None, cached_value=str(value) if value is not None else None)
                finally:
                    values.close()
            finally:
                formulas.close()
        result = Parsed(f"openpyxl/{version('openpyxl')}", segments, message="保留公式和缓存值，不执行或重新计算公式；缓存可能缺失或过期。")
    elif ext == ".dxf":
        import ezdxf
        doc = ezdxf.readfile(path)
        for layout in doc.layouts:
            for entity in layout:
                kind = entity.dxftype()
                details = {"layout": layout.name, "layer": entity.dxf.layer, "entity": kind, "handle": entity.dxf.handle}
                data = {k: str(v) for k, v in entity.dxf.all_existing_dxf_attribs().items()}
                if kind == "MTEXT":
                    data["plain_text"] = entity.plain_text()
                if kind == "DIMENSION":
                    try:
                        data["geometric_measurement"] = str(entity.get_measurement())
                    except Exception:
                        data["measurement_note"] = "此尺寸实体无法取得几何测量值"
                add(f"DXF {layout.name} · 图层 {entity.dxf.layer} · {kind} · 句柄 {entity.dxf.handle}", json.dumps(data, ensure_ascii=False), **details)
        result = Parsed(f"ezdxf/{version('ezdxf')}", segments, message="读取布局顶层实体；块内实体尚未展开。DXF 几何值不自动认定为建筑实测值或物理单位。")
    else:
        add("图片全幅（文件级定位，未执行 OCR）", "", precision="file", needs_ocr=True)
        result = Parsed("image-metadata/1", segments, "needs_ocr", "原件可在资料投喂页预览；OCR 尚未安装，允许人工补充知识并说明位置。")
    if not segments and result.status == "parsed":
        result.message = (result.message + "；" if result.message else "") + "未提取到内容，请检查原件"
    return result
