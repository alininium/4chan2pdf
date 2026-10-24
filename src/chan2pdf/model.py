"""Data model shared by the parsers, the media step and the renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Attachment:
    full_url: str | None
    thumb_url: str | None
    filename: str = ""
    width: int | None = None
    height: int | None = None
    size_text: str = ""  # as shown by the board, e.g. "227 KB"
    spoiler: bool = False
    md5: str | None = None
    # Filled in by the media step.
    local_path: Path | None = None
    source: str | None = None  # "full", "thumb" or None when nothing could be fetched
    kind: str = "image"  # "image", "animated" or "video"
    embedded_size: tuple[int, int] | None = None  # pixels actually put in the PDF
    error: str | None = None

    @property
    def is_video(self) -> bool:
        url = (self.full_url or self.filename or "").lower().split("?")[0]
        return url.endswith((".webm", ".mp4", ".mov", ".mkv"))


@dataclass
class Post:
    id: int
    is_op: bool = False
    subject: str = ""
    name: str = "Anonymous"
    tripcode: str = ""
    poster_id: str = ""
    flag: str = ""
    timestamp: datetime | None = None
    date_text: str = ""
    body_html: str = ""  # raw board markup of the message; cleaned by links.rewrite_bodies
    files: list[Attachment] = field(default_factory=list)
    replies_to: list[int] = field(default_factory=list)
    replied_by: list[int] = field(default_factory=list)


@dataclass
class Thread:
    url: str
    board: str
    id: int
    posts: list[Post]
    board_title: str = ""

    @property
    def op(self) -> Post:
        return self.posts[0]

    @property
    def subject(self) -> str:
        return self.op.subject

    def by_id(self) -> dict[int, Post]:
        return {p.id: p for p in self.posts}
