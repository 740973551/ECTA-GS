"""Local parsing workflow and append-only review/version evidence."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, update
from ecta_gs.models import (Asset, Document, ParseTask, Extraction, KnowledgeClaim,
                           KnowledgeVersion, HumanReview, now)
from ecta_gs.parsers import parse, CATEGORIES, EPISTEMIC_LABELS, REVIEW_LABELS


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def snapshot(claim):
    return {key: getattr(claim, key) for key in ("building_id", "extraction_id", "category", "content", "epistemic", "status", "version")}


class ReviewService:
    def recover_parse_tasks(self):
        # A single process owns the library. A previous process may have exited mid-task.
        with self.sessions.begin() as s:
            for task in s.scalars(select(ParseTask).where(ParseTask.status.in_(["queued", "running"]))):
                task.status, task.message, task.finished_at = "interrupted", "上次解析未完成，可安全重试；已有结果和审核知识保留", now()
                doc = s.get(Document, task.document_id)
                if doc.status in ("queued", "running"):
                    doc.status, doc.error = "interrupted", task.message

    def queue_parsing(self, document_ids):
        ids = list(dict.fromkeys(document_ids))
        with self.sessions.begin() as s:
            documents = []
            for doc_id in ids:
                doc = s.get(Document, doc_id)
                if doc is None:
                    raise ValueError("资料不存在")
                if s.scalar(select(ParseTask.id).where(ParseTask.document_id == doc_id, ParseTask.status.in_(["queued", "running"]))):
                    raise ValueError(f"{doc.filename} 已有解析任务")
                documents.append(doc)
            tasks = []
            for doc in documents:
                task = ParseTask(document_id=doc.id, status="queued")
                s.add(task)
                doc.status, doc.error = "queued", ""
                s.flush()
                tasks.append(task.id)
            return tasks

    def run_parse_task(self, task_id):
        with self.sessions.begin() as s:
            changed = s.execute(update(ParseTask).where(ParseTask.id == task_id, ParseTask.status == "queued").values(status="running"))
            if changed.rowcount != 1:
                raise ValueError("任务不是等待状态，不能重复运行")
            task = s.get(ParseTask, task_id)
            doc = s.get(Document, task.document_id)
            asset = s.get(Asset, doc.asset_id)
            path = self.root / asset.relative_path
            expected_hash, extension = asset.sha256, doc.extension
            doc.status = "running"
        temporary = None
        try:
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
                    raise ValueError("原件 SHA-256 校验失败，已停止解析；请检查独立备份")
            result = parse(path, extension)
            # Each run produces its own artifact; re-parsing never replaces previous evidence.
            artifact = self.root / "extracted" / f"task-{task_id}.json"
            temporary = artifact.with_name(f".{uuid4().hex}.tmp")
            with temporary.open("x", encoding="utf-8") as out:
                out.write(encode({"task_id": task_id, "parser": result.parser, "status": result.status,
                                  "message": result.message, "segments": [vars(p) for p in result.segments]}))
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, artifact)
            with self.sessions.begin() as s:
                task = s.get(ParseTask, task_id)
                doc = s.get(Document, task.document_id)
                s.add_all([Extraction(task_id=task_id, document_id=doc.id, locator=p.locator,
                    text=p.text, details=encode(p.details), parser=result.parser) for p in result.segments])
                task.status, task.parser = result.status, result.parser
                task.message, task.finished_at = result.message, now()
                doc.status, doc.error = result.status, result.message
            return result.status
        except Exception as exc:
            with self.sessions.begin() as s:
                task = s.get(ParseTask, task_id)
                task.status, task.message, task.finished_at = "failed", f"{type(exc).__name__}: {str(exc)[:1000]}", now()
                doc = s.get(Document, task.document_id)
                doc.status, doc.error = "failed", task.message
            return "failed"
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def parse_tasks(self, document_id):
        with self.sessions() as s:
            return list(s.scalars(select(ParseTask).where(ParseTask.document_id == document_id).order_by(ParseTask.id.desc())))

    def extractions(self, document_id, task_id=None):
        with self.sessions() as s:
            stmt = select(Extraction).where(Extraction.document_id == document_id)
            if task_id is not None:
                stmt = stmt.where(Extraction.task_id == task_id)
            return list(s.scalars(stmt.order_by(Extraction.id)))

    def create_pending_claim(self, extraction_id, reviewer="本地研究者", reason="人工从原文选取，待审核"):
        if not reviewer.strip() or not reason.strip():
            raise ValueError("请填写修改者和理由")
        with self.sessions.begin() as s:
            extraction = s.get(Extraction, extraction_id)
            if extraction is None:
                raise ValueError("来源片段不存在")
            doc = s.get(Document, extraction.document_id)
            if doc.building_id is None:
                raise ValueError("请先在资料投喂页为此文件指定建筑，再整理知识")
            claim = KnowledgeClaim(building_id=doc.building_id, extraction_id=extraction_id,
                category="其他", content=extraction.text, epistemic="hypothesis", status="pending_review", version=1)
            s.add(claim)
            s.flush()
            self._record_review(s, claim, {}, reviewer, reason)
            return claim.id

    @staticmethod
    def _record_review(s, claim, before, reviewer, reason):
        after = snapshot(claim)
        version = KnowledgeVersion(claim_id=claim.id, version=claim.version, snapshot=encode(after))
        s.add(version)
        s.flush()
        s.add(HumanReview(claim_id=claim.id, version_id=version.id, before=encode(before), after=encode(after),
            reviewer=reviewer.strip(), reason=reason.strip()))

    def review_claim(self, claim_id, expected_version, *, content, category, epistemic, status, reviewer, reason):
        if category not in CATEGORIES or epistemic not in EPISTEMIC_LABELS or status not in REVIEW_LABELS:
            raise ValueError("无效知识类型、事实状态或审核状态")
        if not content.strip() or not reviewer.strip() or not reason.strip():
            raise ValueError("内容、修改者、审核理由均不能为空；推导或类比请在理由中写明方法和依据")
        with self.sessions.begin() as s:
            claim = s.get(KnowledgeClaim, claim_id)
            if claim is None:
                raise ValueError("知识条目不存在")
            before = snapshot(claim)
            # Optimistic concurrency prevents stale editors silently overwriting corrections.
            changed = s.execute(update(KnowledgeClaim).where(KnowledgeClaim.id == claim_id,
                KnowledgeClaim.version == expected_version).values(content=content.strip(), category=category,
                    epistemic=epistemic, status=status, version=expected_version + 1, updated_at=now()),
                execution_options={"synchronize_session": False})
            if changed.rowcount != 1:
                raise ValueError("条目已被其他操作修改，请刷新后重审；本次未覆盖已有修订")
            s.refresh(claim)
            self._record_review(s, claim, before, reviewer, reason)

    def claims(self, building_id="all", status="all", query="", document_id=None):
        with self.sessions() as s:
            stmt = select(KnowledgeClaim, Extraction, Document).join(Extraction, KnowledgeClaim.extraction_id == Extraction.id).join(Document, Extraction.document_id == Document.id)
            if building_id != "all":
                stmt = stmt.where(KnowledgeClaim.building_id == building_id)
            if status != "all":
                stmt = stmt.where(KnowledgeClaim.status == status)
            if query:
                stmt = stmt.where(KnowledgeClaim.content.contains(query, autoescape=True))
            if document_id is not None:
                stmt = stmt.where(Document.id == document_id)
            return list(s.execute(stmt.order_by(KnowledgeClaim.id.desc())))

    def review_history(self, claim_id):
        with self.sessions() as s:
            return list(s.scalars(select(HumanReview).where(HumanReview.claim_id == claim_id).order_by(HumanReview.id)))
