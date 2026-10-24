"""Keep downloaded threads on disk so a collection can grow without re-downloading.

Layout of a work folder (default: `<output>_files/` next to the PDF):

    index.json          {"version": 1, "settings": {...}, "threads": {url: file}}
    threads/<file>.json  one parsed thread, bodies already cleaned, image state included
    images/              images prepared for embedding (named by content hash)
    media/               saved videos and animated GIFs that the PDF links to

Paths inside the JSON are relative to the work folder when they are inside it,
so the folder can be moved together with the PDF.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path

from chan2pdf.model import Attachment, Post, Thread

log = logging.getLogger(__name__)
VERSION = 1
LOCAL_FIELDS = ("local_path", "source", "kind", "embedded_size", "saved_path", "error")


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".part-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.images = root / "images"
        self.media = root / "media"
        self._index_path = root / "index.json"
        self.index: dict = {"version": VERSION, "settings": {}, "threads": {}}
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                if data.get("version") == VERSION:
                    self.index = data
                else:
                    log.warning("ignoring %s: written by another version", self._index_path)
            except (OSError, ValueError) as e:
                log.warning("ignoring unreadable %s: %s", self._index_path, e)

    # Paths ----------------------------------------------------------------

    def _rel(self, path: Path | None) -> str | None:
        if path is None:
            return None
        path = path.resolve()
        try:
            return path.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return str(path)

    def _abs(self, value: str | None) -> Path | None:
        if value is None:
            return None
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    # Settings that change how images are prepared ----------------------------

    def settings_changed(self, settings: dict) -> bool:
        """Record the image settings; True if earlier threads were prepared differently."""
        old = self.index.get("settings")
        self.index["settings"] = settings
        return bool(old) and old != settings and bool(self.index["threads"])

    # Threads ----------------------------------------------------------------

    def _file_for(self, url: str, thread: Thread) -> str:
        existing = self.index["threads"].get(url)
        if existing:
            return existing
        base = re.sub(r"[^\w]+", "_", f"{thread.board}_{thread.id}")
        name, n = f"{base}.json", 2
        taken = set(self.index["threads"].values())
        while name in taken:
            name, n = f"{base}_{n}.json", n + 1
        return name

    def load(self, url: str) -> Thread | None:
        name = self.index["threads"].get(url)
        if not name:
            return None
        try:
            data = json.loads((self.root / "threads" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("re-downloading %s: saved copy unreadable (%s)", url, e)
            return None
        return self._thread_from(data)

    def save(self, url: str, thread: Thread) -> None:
        name = self._file_for(url, thread)
        _write_json(self.root / "threads" / name, self._thread_to(thread))
        self.index["threads"][url] = name
        _write_json(self._index_path, self.index)

    # (De)serialization -------------------------------------------------------

    def _att_to(self, att: Attachment) -> dict:
        d = asdict(att)
        d["local_path"] = self._rel(att.local_path)
        d["saved_path"] = self._rel(att.saved_path)
        return d

    def _att_from(self, d: dict) -> Attachment:
        known = {f.name for f in fields(Attachment)}
        d = {k: v for k, v in d.items() if k in known}
        att = Attachment(**d)
        att.local_path = self._abs(d.get("local_path"))
        att.saved_path = self._abs(d.get("saved_path"))
        if att.embedded_size is not None:
            att.embedded_size = tuple(att.embedded_size)
        # A prepared file that has gone missing is fetched again.
        if att.local_path is not None and not att.local_path.exists():
            reset_attachment(att)
        if att.saved_path is not None and not att.saved_path.exists():
            reset_attachment(att)
        return att

    def _thread_to(self, thread: Thread) -> dict:
        posts = []
        for p in thread.posts:
            d = {f.name: getattr(p, f.name) for f in fields(Post) if f.name != "files"}
            d["timestamp"] = p.timestamp.isoformat() if p.timestamp else None
            d["files"] = [self._att_to(a) for a in p.files]
            posts.append(d)
        return {
            "url": thread.url,
            "board": thread.board,
            "id": thread.id,
            "board_title": thread.board_title,
            "posts": posts,
        }

    def _thread_from(self, data: dict) -> Thread:
        posts = []
        for d in data["posts"]:
            files = [self._att_from(a) for a in d.pop("files", [])]
            ts = d.pop("timestamp", None)
            post = Post(**d, files=files)
            post.timestamp = datetime.fromisoformat(ts) if ts else None
            posts.append(post)
        return Thread(
            url=data["url"],
            board=data["board"],
            id=data["id"],
            posts=posts,
            board_title=data.get("board_title", ""),
        )


def reset_attachment(att: Attachment) -> None:
    """Forget everything the media step filled in, so the file is fetched again."""
    att.local_path = att.saved_path = att.source = att.error = att.embedded_size = None
    att.kind = "image"


def carry_over(old: Thread | None, new: Thread) -> None:
    """Reuse already-downloaded images when a thread is re-fetched (--refresh).

    Images that failed before are left unset so they are tried again.
    """
    if old is None:
        return
    done = {
        a.full_url or a.thumb_url: a for p in old.posts for a in p.files if a.local_path is not None
    }
    for p in new.posts:
        for att in p.files:
            prev = done.get(att.full_url or att.thumb_url)
            if prev is not None:
                for name in LOCAL_FIELDS + ("width", "height"):
                    setattr(att, name, getattr(prev, name))
