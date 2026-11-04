from datetime import datetime, timezone
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    aliases: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str] = mapped_column(Text, default="")
    period: Mapped[str] = mapped_column(String(120), default="")
    building_type: Mapped[str] = mapped_column(String(120), default="")
    dating_basis: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[str] = mapped_column(default=now, onupdate=now)


class BuildingPhase(Base):
    __tablename__ = "building_phases"
    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    kind: Mapped[str] = mapped_column(String(40))
    date_statement: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")


class Asset(Base):
    __tablename__ = "source_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    relative_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int]
    imported_at: Mapped[str] = mapped_column(default=now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("asset_id", "scope", name="uq_asset_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("source_assets.id"))
    building_id: Mapped[int | None] = mapped_column(ForeignKey("buildings.id"), nullable=True)
    # scope makes NULL (unassigned) participate in duplicate protection on SQLite.
    scope: Mapped[int] = mapped_column(default=0)
    filename: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(40))
    source_note: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="saved_unparsed")
    error: Mapped[str] = mapped_column(Text, default="")
    imported_at: Mapped[str] = mapped_column(default=now)
