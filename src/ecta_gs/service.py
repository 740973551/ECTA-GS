"""Application services; no Qt or external AI dependencies."""
import hashlib
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from uuid import uuid4
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from platformdirs import user_data_path
from sqlalchemy import create_engine, event, select, or_, func
from sqlalchemy.orm import sessionmaker
from ecta_gs.models import Building, BuildingPhase, Asset, Document, KnowledgeClaim, Extraction, ParseTask
from ecta_gs.review_service import ReviewService

log = logging.getLogger(__name__)


class Library(ReviewService):
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("ECTA_GS_DATA_DIR") or user_data_path("ECTA-GS", appauthor=False)).resolve()
        for folder in ("database", "library/objects", "library/.incoming", "extracted", "knowledge", "backups", "exports", "logs"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "database/ecta.sqlite3"
        self.engine = create_engine("sqlite:///" + self.db_path.as_posix(), connect_args={"timeout": 30})
        @event.listens_for(self.engine, "connect")
        def configure(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            dbapi_connection.execute("PRAGMA synchronous=FULL")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._import_lock = threading.Lock()
        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        target_version = ScriptDirectory.from_config(cfg).get_current_head()
        if self.db_path.exists():
            with sqlite3.connect(self.db_path) as con:
                try:
                    version = con.execute("select version_num from alembic_version").fetchone()
                except sqlite3.OperationalError:
                    version = None
            if version != (target_version,):
                self.backup_database()
        with self.engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        self.recover_parse_tasks()

    def backup_database(self):
        target = self.root / "backups" / (uuid4().hex + ".sqlite3")
        with sqlite3.connect(self.db_path) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        return target

    def buildings(self, query=""):
        with self.sessions() as s:
            stmt = select(Building).order_by(Building.updated_at.desc(), Building.id.desc())
            if query:
                stmt = stmt.where(or_(*(getattr(Building, key).contains(query, autoescape=True) for key in ("name", "aliases", "location", "period"))))
            return list(s.scalars(stmt))

    def save_building(self, building_id=None, **fields):
        allowed = {"name", "aliases", "location", "period", "building_type", "dating_basis", "archived"}
        if set(fields) - allowed:
            raise ValueError("未知建筑字段")
        with self.sessions.begin() as s:
            row = s.get(Building, building_id) if building_id else Building()
            if row is None:
                raise ValueError("建筑不存在")
            if not str(fields.get("name", row.name or "")).strip():
                raise ValueError("建筑名称不能为空")
            for key, value in fields.items():
                setattr(row, key, value.strip() if isinstance(value, str) else value)
            s.add(row)
            s.flush()
            return row.id

    def phases(self, building_id):
        with self.sessions() as s:
            return list(s.scalars(select(BuildingPhase).where(BuildingPhase.building_id == building_id)))

    def add_phase(self, building_id, kind, date_statement, evidence):
        if kind not in ("初建", "重建", "修缮", "替换", "未知"):
            raise ValueError("无效阶段类型")
        with self.sessions.begin() as s:
            s.add(BuildingPhase(building_id=building_id, kind=kind, date_statement=date_statement, evidence=evidence))

    def documents(self, building_id="all"):
        with self.sessions() as s:
            stmt = select(Document, Asset).join(Asset).order_by(Document.id.desc())
            if building_id != "all":
                stmt = stmt.where(Document.building_id == building_id)
            return list(s.execute(stmt))

    def _validate_building(self, s, building_id):
        if building_id is not None and s.get(Building, building_id) is None:
            raise ValueError("所属建筑不存在")

    def import_file(self, path, building_id=None, source_note="", notes=""):
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("仅导入普通文件，符号链接不会导入")
        # Never recursively ingest the application's own data library.
        if path.resolve().is_relative_to(self.root):
            raise ValueError("不能导入资料库内部文件")
        with self._import_lock, self.sessions.begin() as s:
            self._validate_building(s, building_id)
            fd, temporary = tempfile.mkstemp(dir=self.root / "library/.incoming")
            temp = Path(temporary)
            try:
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as out, path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        out.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                sha = digest.hexdigest()
                asset = s.scalar(select(Asset).where(Asset.sha256 == sha))
                relative = f"library/objects/{sha[:2]}/{sha}"
                destination = self.root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    with destination.open("rb") as existing:
                        if hashlib.file_digest(existing, "sha256").hexdigest() != sha:
                            raise ValueError("资料库原件校验失败，已停止导入；请检查备份")
                else:
                    # Exclusive creation by hard link: an existing original is never overwritten.
                    os.link(temp, destination)
                if asset is None:
                    asset = Asset(sha256=sha, relative_path=relative, size=size)
                    s.add(asset)
                    s.flush()
                old = s.scalar(select(Document).where(Document.asset_id == asset.id, Document.scope == (building_id or 0)))
                if old:
                    return old.id, True
                doc = Document(asset_id=asset.id, building_id=building_id, scope=building_id or 0,
                    filename=path.name, extension=path.suffix.lower(), source_note=source_note, notes=notes)
                s.add(doc)
                s.flush()
                return doc.id, False
            finally:
                temp.unlink(missing_ok=True)

    def update_document(self, doc_id, building_id, source_note, notes):
        with self.sessions.begin() as s:
            self._validate_building(s, building_id)
            doc = s.get(Document, doc_id)
            if doc is None:
                raise ValueError("资料不存在")
            if doc.building_id != building_id and s.scalar(select(KnowledgeClaim.id).join(Extraction, KnowledgeClaim.extraction_id == Extraction.id).where(Extraction.document_id == doc_id).limit(1)):
                raise ValueError("此资料已有审核条目，不能直接改变归属；请将原文件另行导入目标建筑，保留现有知识的来源归属")
            duplicate = s.scalar(select(Document).where(Document.asset_id == doc.asset_id, Document.scope == (building_id or 0), Document.id != doc_id))
            if duplicate:
                raise ValueError("目标建筑已有相同原件；原归属和备注已保留")
            doc.building_id, doc.scope = building_id, building_id or 0
            doc.source_note, doc.notes = source_note, notes

    def counts(self):
        with self.sessions() as s:
            return {"建筑档案": s.scalar(select(func.count()).select_from(Building)),
                    "原始文件": s.scalar(select(func.count()).select_from(Asset)),
                    "资料关联": s.scalar(select(func.count()).select_from(Document)),
                    "尚未解析": s.scalar(select(func.count()).select_from(Document).where(Document.status == "saved_unparsed")),
                    "待解析任务": s.scalar(select(func.count()).select_from(ParseTask).where(ParseTask.status.in_(["queued", "running"]))),
                    "解析失败": s.scalar(select(func.count()).select_from(Document).where(Document.status == "failed")),
                    "待审核": s.scalar(select(func.count()).select_from(KnowledgeClaim).where(KnowledgeClaim.status == "pending_review")),
                    "已确认知识": s.scalar(select(func.count()).select_from(KnowledgeClaim).where(KnowledgeClaim.status == "approved"))}

    def close(self):
        self.engine.dispose()


def expand_paths(paths, excluded_root=None):
    """Yield regular files, skip symlinks and data root; report traversal errors."""
    excluded = Path(excluded_root).resolve() if excluded_root else None
    seen = set()
    for raw in paths:
        path = Path(raw)
        if path.is_symlink() or (excluded and path.resolve().is_relative_to(excluded)):
            continue
        if path.is_dir():
            def onerror(error):
                raise error
            for base, dirs, files in os.walk(path, followlinks=False, onerror=onerror):
                dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink() and not (excluded and (Path(base) / d).resolve().is_relative_to(excluded))]
                for name in files:
                    item = Path(base) / name
                    if not item.is_symlink() and item.resolve() not in seen:
                        seen.add(item.resolve())
                        yield item
        elif path.resolve() not in seen:
            seen.add(path.resolve())
            yield path
