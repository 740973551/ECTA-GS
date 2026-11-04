import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy import text
from ecta_gs.service import Library, expand_paths


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "data")
    yield lib
    lib.close()


def sample(tmp_path, name="资料.txt", content="柱高：待考，非实测。"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_archive_search_phase_and_restart(library):
    root = library.root
    first = library.save_building(name="佛光寺东大殿", period="未知", dating_basis="待考")
    second = library.save_building(name="南禅寺大殿")
    library.add_phase(first, "修缮", "待考", "待补来源")
    library.save_building(first, name="佛光寺东大殿", aliases="测试别名", archived=True)
    library.close()
    reopened = Library(root)
    assert reopened.buildings("测试别名")[0].archived
    assert reopened.buildings("南禅")[0].id == second
    assert len(reopened.phases(first)) == 1
    assert reopened.phases(second) == []
    reopened.close()


def test_blank_name_and_missing_building_rejected(library, tmp_path):
    with pytest.raises(ValueError):
        library.save_building(name="  ")
    with pytest.raises(ValueError):
        library.save_building(999, name="不存在")
    with pytest.raises(ValueError):
        library.import_file(sample(tmp_path), 999)
    assert library.documents() == []


def test_import_immutable_dedup_and_reopen(library, tmp_path):
    path = sample(tmp_path)
    raw = path.read_bytes()
    doc_id, duplicate = library.import_file(path, source_note="测试来源", notes="未知归属")
    assert not duplicate
    assert library.import_file(path) == (doc_id, True)
    assert path.read_bytes() == raw
    doc, asset = library.documents(None)[0]
    assert doc.source_note == "测试来源"
    assert asset.sha256 == hashlib.sha256(raw).hexdigest()
    assert (library.root / asset.relative_path).read_bytes() == raw
    assert len(list((library.root / "library/.incoming").iterdir())) == 0
    root = library.root
    library.close()
    path.unlink()
    reopened = Library(root)
    d, a = reopened.documents(None)[0]
    assert d.id == doc_id and d.status == "saved_unparsed"
    assert (root / a.relative_path).read_bytes() == raw
    reopened.close()


def test_shared_asset_isolated_assignments_and_metadata(library, tmp_path):
    first = library.save_building(name="佛光寺")
    second = library.save_building(name="南禅寺")
    path = sample(tmp_path)
    doc, _ = library.import_file(path, first)
    other, _ = library.import_file(path, second)
    assert doc != other
    assert len(library.documents(first)) == len(library.documents(second)) == 1
    assert library.counts()["原始文件"] == 1
    library.update_document(doc, None, "新来源", "备注")
    assert library.documents(first) == []
    assert library.documents(None)[0][0].notes == "备注"
    with pytest.raises(ValueError, match="已有相同原件"):
        library.update_document(doc, second, "不应写入", "不应覆盖")
    assert library.documents(None)[0][0].source_note == "新来源"


def test_same_filename_different_content(library, tmp_path):
    path = sample(tmp_path)
    library.import_file(path)
    path.write_text("不同资料", encoding="utf-8")
    library.import_file(path)
    assert library.counts()["原始文件"] == 2


def test_failed_copy_does_not_create_document(library, tmp_path):
    path = sample(tmp_path)
    with patch("ecta_gs.service.os.fsync", side_effect=OSError("模拟磁盘错误")):
        with pytest.raises(OSError):
            library.import_file(path)
    assert library.documents() == []
    assert not list((library.root / "library/.incoming").iterdir())
    assert path.exists()


def test_crash_after_copy_orphan_can_be_recovered(library, tmp_path):
    path = sample(tmp_path)
    with patch("ecta_gs.service.Document.__init__", side_effect=RuntimeError("模拟复制后中断")):
        with pytest.raises(RuntimeError):
            library.import_file(path)
    assert library.documents() == []
    assert len(list((library.root / "library/objects").glob("*/*"))) == 1
    library.import_file(path)
    assert library.counts()["原始文件"] == 1


def test_tampered_object_never_overwritten(library, tmp_path):
    path = sample(tmp_path)
    library.import_file(path)
    _, asset = library.documents()[0]
    stored = library.root / asset.relative_path
    stored.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="校验失败"):
        library.import_file(path)
    assert stored.read_bytes() == b"damaged"


def test_folder_excludes_symlinks_and_own_library(library, tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    file = sample(folder)
    nested = folder / "nested"
    nested.mkdir()
    second = sample(nested, "未知格式.dwg")
    try:
        (folder / "link").symlink_to(folder, target_is_directory=True)
    except OSError:
        pass
    found = list(expand_paths([folder, file], library.root))
    assert set(found) == {file, second}
    for path in found:
        library.import_file(path)
    assert all(d.status == "saved_unparsed" for d, _ in library.documents())
    assert list(expand_paths([library.root], library.root)) == []
    with pytest.raises(ValueError):
        library.import_file(library.db_path)


def test_sqlite_foreign_keys_and_backup(library):
    library.save_building(name="持久化测试")
    with library.engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("select version_num from alembic_version")).scalar() == "0001"
    backup = library.backup_database()
    with sqlite3.connect(backup) as con:
        assert con.execute("select name from buildings").fetchone()[0] == "持久化测试"


def test_unversioned_database_is_backed_up(tmp_path):
    root = tmp_path / "legacy"
    (root / "database").mkdir(parents=True)
    db = root / "database/ecta.sqlite3"
    with sqlite3.connect(db) as con:
        con.execute("create table legacy_notes(note text)")
        con.execute("insert into legacy_notes values ('保留旧资料')")
    lib = Library(root)
    backups = list((root / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as con:
        assert con.execute("select note from legacy_notes").fetchone()[0] == "保留旧资料"
    lib.close()


def test_persistence_across_python_processes(tmp_path):
    import subprocess
    import sys
    root = tmp_path / "process-data"
    source = sample(tmp_path)
    writer = """
import sys
from ecta_gs.service import Library
lib = Library(sys.argv[1])
building = lib.save_building(name='跨进程档案')
lib.import_file(sys.argv[2], building, source_note='跨进程来源')
lib.add_phase(building, '未知', '待考', '待补证据')
lib.close()
"""
    reader = """
import sys
from ecta_gs.service import Library
lib = Library(sys.argv[1])
building = lib.buildings('跨进程')[0]
doc, asset = lib.documents(building.id)[0]
assert doc.source_note == '跨进程来源'
assert (lib.root / asset.relative_path).read_text(encoding='utf-8') == '柱高：待考，非实测。'
assert len(lib.phases(building.id)) == 1
lib.close()
"""
    subprocess.run([sys.executable, "-c", writer, str(root), str(source)], check=True)
    source.unlink()
    subprocess.run([sys.executable, "-c", reader, str(root)], check=True)
