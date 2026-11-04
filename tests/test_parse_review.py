import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch
import pytest
from sqlalchemy import text
from ecta_gs.service import Library
from ecta_gs.parsers import parse
from ecta_gs.models import KnowledgeVersion


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "data")
    yield lib
    lib.close()


def ingest(library, tmp_path, name="证据.txt", content="柱高 3.2 米，资料记载，测量方法待考", building=None):
    if building is None:
        building = library.save_building(name="建筑甲")
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    doc_id, _ = library.import_file(path, building)
    task = library.queue_parsing([doc_id])[0]
    return doc_id, task, path


def test_text_review_reparse_and_restart_isolation(library, tmp_path):
    doc_id, task, path = ingest(library, tmp_path)
    raw = path.read_bytes()
    assert library.run_parse_task(task) == "parsed"
    evidence = library.extractions(doc_id)[0]
    assert evidence.locator == "行 1"
    claim_id = library.create_pending_claim(evidence.id)
    claim = library.claims()[0][0]
    assert claim.status == "pending_review" and claim.epistemic == "hypothesis"
    library.review_claim(claim_id, 1, content="柱高记载为 3.2 米，测量方法未知", category="尺寸", epistemic="measured", status="approved", reviewer="研究者甲", reason="逐字核对原文；直接记载，尚需核实测量位置")
    second = library.save_building(name="建筑乙")
    assert library.claims(building_id=second) == []
    assert len(library.claims(status="approved", query="3.2")) == 1
    new_task = library.queue_parsing([doc_id])[0]
    assert library.run_parse_task(new_task) == "parsed"
    assert len(library.extractions(doc_id)) == 2
    assert library.claims()[0][1].id == evidence.id
    assert library.claims()[0][0].status == "approved"
    assert library.extractions(doc_id)[0].text == evidence.text
    assert path.read_bytes() == raw
    artifact = json.loads((library.root / f"extracted/task-{task}.json").read_text())
    assert artifact["segments"][0]["locator"] == "行 1"
    root = library.root
    library.close()
    reopened = Library(root)
    reviews = reopened.review_history(claim_id)
    assert len(reviews) == 2
    assert json.loads(reviews[1].before)["content"] == evidence.text
    assert json.loads(reviews[1].after)["content"] == "柱高记载为 3.2 米，测量方法未知"
    assert reviews[1].reviewer == "研究者甲"
    with reopened.sessions() as s:
        assert s.query(KnowledgeVersion).count() == 2
    assert len(reopened.claims(status="approved")) == 1
    reopened.close()


def test_stale_review_rejected_and_rejection_dispute_retained(library, tmp_path):
    doc_id, task, _ = ingest(library, tmp_path)
    library.run_parse_task(task)
    claim_id = library.create_pending_claim(library.extractions(doc_id)[0].id)
    payload = dict(content="单位可能有误", category="尺寸", epistemic="hypothesis", reviewer="甲", reason="单位尚待核对")
    library.review_claim(claim_id, 1, status="rejected", **payload)
    with pytest.raises(ValueError, match="其他操作修改"):
        library.review_claim(claim_id, 1, status="approved", **payload)
    assert library.claims()[0][0].status == "rejected"
    library.review_claim(claim_id, 2, status="disputed", **payload)
    assert len(library.review_history(claim_id)) == 3
    assert library.claims(status="approved") == []
    with pytest.raises(ValueError, match="不能为空"):
        library.review_claim(claim_id, 3, status="approved", **{**payload, "reason": ""})
    assert library.claims()[0][0].version == 3


def test_reassignment_cannot_move_reviewed_knowledge(library, tmp_path):
    doc_id, task, _ = ingest(library, tmp_path)
    library.run_parse_task(task)
    claim_id = library.create_pending_claim(library.extractions(doc_id)[0].id)
    other = library.save_building(name="乙")
    with pytest.raises(ValueError, match="已有审核条目"):
        library.update_document(doc_id, other, "新来源", "新备注")
    assert library.claims()[0][0].id == claim_id
    assert library.claims(building_id=other) == []


def test_unassigned_needs_building_before_claim(library, tmp_path):
    path = tmp_path / "未分配.txt"
    path.write_text("原文", encoding="utf-8")
    doc_id, _ = library.import_file(path)
    library.run_parse_task(library.queue_parsing([doc_id])[0])
    with pytest.raises(ValueError, match="指定建筑"):
        library.create_pending_claim(library.extractions(doc_id)[0].id)


def test_failed_unsupported_and_interrupted_task_status(library, tmp_path):
    invalid, bad_task, _ = ingest(library, tmp_path, "损坏.pdf", "这不是 PDF")
    assert library.run_parse_task(bad_task) == "failed"
    assert library.extractions(invalid) == []
    assert library.parse_tasks(invalid)[0].message
    unknown, unsupported, _ = ingest(library, tmp_path, "图.dwg")
    assert library.run_parse_task(unsupported) == "unsupported"
    assert library.extractions(unknown) == []
    queued, task, _ = ingest(library, tmp_path)
    with pytest.raises(ValueError, match="已有解析任务"):
        library.queue_parsing([queued])
    root = library.root
    library.close()
    reopened = Library(root)
    assert reopened.parse_tasks(queued)[0].status == "interrupted"
    retry = reopened.queue_parsing([queued])[0]
    assert reopened.run_parse_task(retry) == "parsed"
    with pytest.raises(ValueError, match="不能重复运行"):
        reopened.run_parse_task(retry)
    reopened.close()


def test_artifact_failure_leaves_no_partial_extractions(library, tmp_path):
    doc_id, task, _ = ingest(library, tmp_path)
    with patch("ecta_gs.review_service.os.replace", side_effect=OSError("磁盘错误")):
        assert library.run_parse_task(task) == "failed"
    assert library.extractions(doc_id) == []
    assert library.claims() == []
    assert not list((library.root / "extracted").glob("*.tmp"))


def test_tampered_source_stops_parsing(library, tmp_path):
    doc_id, task, _ = ingest(library, tmp_path)
    _, asset = library.documents()[0]
    (library.root / asset.relative_path).write_bytes(b"bad")
    assert library.run_parse_task(task) == "failed"
    assert "SHA-256" in library.parse_tasks(doc_id)[0].message


def test_pdf_pages_tables_and_scanned_page(tmp_path):
    import pymupdf
    path = tmp_path / "两页.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((70, 70), "Beam length 3.2 m")
        pdf.new_page()
        pdf.save(path)
    result = parse(path, ".pdf")
    assert result.status == "needs_ocr"
    assert "3.2" in result.segments[0].text
    assert result.segments[0].details["page"] == 1
    assert result.segments[1].details["page"] == 2
    assert result.segments[1].details["needs_ocr"]


def test_docx_paragraphs_table_and_no_fabricated_pages(tmp_path):
    from docx import Document
    path = tmp_path / "资料.docx"
    doc = Document()
    doc.add_heading("构件资料", level=1)
    doc.add_paragraph("柱高待考")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "柱"
    table.cell(0, 1).text = "3.2m"
    doc.save(path)
    result = parse(path, ".docx")
    assert len(result.segments) == 4
    assert result.segments[0].details["style"] == "Heading 1"
    assert result.segments[-1].locator == "Word 表 1 · 行 1 列 2"
    assert all("page" not in segment.details for segment in result.segments)


def test_xlsx_sheets_cells_formula_and_cache(tmp_path):
    from openpyxl import Workbook
    path = tmp_path / "资料.xlsx"
    book = Workbook()
    book.active.title = "尺寸"
    book.active["B2"] = 3.2
    book.active["C2"] = "=B2*1000"
    book.create_sheet("其他")["A1"] = "待考"
    book.save(path)
    result = parse(path, ".xlsx")
    assert len(result.segments) == 3
    assert result.segments[0].locator == "工作表 尺寸 · B2"
    formula = result.segments[1]
    assert formula.details["formula"] == "=B2*1000"
    assert formula.details["cached_value"] is None
    assert "无缓存值" in formula.text
    assert result.segments[-1].details["sheet"] == "其他"


def test_dxf_layers_entity_handle_and_dimension(tmp_path):
    import ezdxf
    path = tmp_path / "图.dxf"
    drawing = ezdxf.new()
    drawing.layers.new("梁架")
    space = drawing.modelspace()
    entity = space.add_text("BEAM", dxfattribs={"layer": "梁架"})
    space.add_line((0, 0), (100, 0))
    dim = space.add_linear_dim(base=(0, 2), p1=(0, 0), p2=(100, 0))
    dim.render()
    drawing.saveas(path)
    result = parse(path, ".dxf")
    match = next(s for s in result.segments if s.details["handle"] == entity.dxf.handle)
    assert match.details["layer"] == "梁架" and match.details["entity"] == "TEXT"
    assert "BEAM" in match.text
    dimension = next(s for s in result.segments if s.details["entity"] == "DIMENSION")
    assert "geometric_measurement" in json.loads(dimension.text)


def test_csv_multiline_json_validation_utf16_and_images(tmp_path):
    csv = tmp_path / "资料.csv"
    csv.write_text('构件;说明\n柱;"第一行\n第二行"\n', encoding="utf-8-sig")
    result = parse(csv, ".csv")
    assert result.segments[1].locator == "CSV 记录 2 · 行 2–3"
    assert result.segments[0].details["delimiter"] == ";"
    data = tmp_path / "资料.json"
    data.write_text('{"value": 3.2}', encoding="utf-8")
    assert "3.2" in parse(data, ".json").segments[0].text
    data.write_text('{invalid}', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        parse(data, ".json")
    text_path = tmp_path / "utf16.txt"
    text_path.write_text("原文\n第二行", encoding="utf-16")
    assert parse(text_path, ".txt").segments[1].text == "第二行"
    image = tmp_path / "image.png"
    image.write_bytes(b"placeholder")
    result = parse(image, ".png")
    assert result.status == "needs_ocr" and result.segments[0].text == ""


def test_0001_upgrade_backups_and_preserves_real_data(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from ecta_gs import service
    root = tmp_path / "old-data"
    (root / "database").mkdir(parents=True)
    db = root / "database/ecta.sqlite3"
    engine = create_engine(f"sqlite:///{db}")
    config = Config()
    config.set_main_option("script_location", str(Path(service.__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
    engine.dispose()
    raw = b"original research file"
    sha = hashlib.sha256(raw).hexdigest()
    rel = f"library/objects/{sha[:2]}/{sha}"
    path = root / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    with sqlite3.connect(db) as con:
        con.execute("insert into buildings values (1, '旧档案', '', '', '', '', '', 0, '2026-11-04')")
        con.execute("insert into source_assets values (1, ?, ?, ?, '2026-11-04')", (sha, rel, len(raw)))
        con.execute("insert into documents values (1, 1, 1, 1, '资料.txt', '.txt', '旧来源', '旧备注', 'saved_unparsed', '', '2026-11-04')")
    lib = Library(root)
    assert lib.buildings()[0].name == "旧档案"
    assert lib.documents()[0][0].source_note == "旧来源"
    assert path.read_bytes() == raw
    backup = next((root / "backups").glob("*.sqlite3"))
    with sqlite3.connect(backup) as con:
        assert con.execute("select version_num from alembic_version").fetchone() == ("0001",)
        assert con.execute("select name from buildings").fetchone() == ("旧档案",)
    with lib.engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar() == "0002"
    lib.close()


@pytest.mark.parametrize("extension", [".pdf", ".docx", ".xlsx", ".csv", ".dxf", ".json"])
def test_parsers_read_content_addressed_extensionless_originals(library, tmp_path, extension):
    path = tmp_path / ("资料" + extension)
    if extension == ".pdf":
        import pymupdf
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((70, 70), "Evidence")
            pdf.save(path)
    elif extension == ".docx":
        from docx import Document
        word = Document()
        word.add_paragraph("原文证据")
        word.save(path)
    elif extension == ".xlsx":
        from openpyxl import Workbook
        book = Workbook()
        book.active["A1"] = "原文证据"
        book.save(path)
    elif extension == ".dxf":
        import ezdxf
        drawing = ezdxf.new()
        drawing.modelspace().add_text("Evidence")
        drawing.saveas(path)
    else:
        path.write_text('{"evidence":"原文"}' if extension == ".json" else '名称,单位\n柱,未知', encoding="utf-8")
    before = path.read_bytes()
    doc_id, _ = library.import_file(path)
    task = library.queue_parsing([doc_id])[0]
    assert library.run_parse_task(task) == "parsed", library.parse_tasks(doc_id)[0].message
    assert library.extractions(doc_id)
    _, asset = library.documents()[0]
    assert (library.root / asset.relative_path).read_bytes() == before


def test_review_corrections_survive_new_process(tmp_path):
    import subprocess
    import sys
    root = tmp_path / "memory"
    lib = Library(root)
    doc_id, task, _ = ingest(lib, tmp_path)
    lib.run_parse_task(task)
    claim_id = lib.create_pending_claim(lib.extractions(doc_id)[0].id)
    lib.review_claim(claim_id, 1, content="柱高未知，原文数值存在争议", category="尺寸", epistemic="hypothesis", status="disputed", reviewer="研究者", reason="修正误读，需复核原图")
    lib.close()
    code = """
import sys
from ecta_gs.service import Library
lib = Library(sys.argv[1])
claim, evidence, doc = lib.claims(query='柱高未知')[0]
assert claim.status == 'disputed'
assert evidence.locator == '行 1'
assert doc.filename == '证据.txt'
assert lib.review_history(claim.id)[-1].reason == '修正误读，需复核原图'
assert lib.claims(status='approved') == []
lib.close()
"""
    subprocess.run([sys.executable, "-c", code, str(root)], check=True)


def test_pdf_table_preserves_page_and_bounds(tmp_path):
    import pymupdf
    path = tmp_path / "table.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for x in (50, 180, 310):
            page.draw_line((x, 100), (x, 180))
        for y in (100, 140, 180):
            page.draw_line((50, y), (310, y))
        page.insert_text((60, 125), "Name")
        page.insert_text((190, 125), "Value")
        page.insert_text((60, 165), "Column")
        page.insert_text((190, 165), "Unknown")
        pdf.save(path)
    result = parse(path, ".pdf")
    table = next(segment for segment in result.segments if segment.details.get("table"))
    assert table.details["page"] == 1
    assert table.details["bbox"] == [50.0, 100.0, 310.0, 180.0]
    assert "Unknown" in table.text
