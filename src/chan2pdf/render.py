"""Render threads to HTML and then to PDF with WeasyPrint.

One thread: posts are anchored as #p<id>. Several threads (a collection): each
thread is a section #t<n> and its posts #t<n>-p<id>, so post numbers from
different boards cannot collide.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html import escape
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

from chan2pdf.model import Attachment, Post, Thread


def _snippet(post: Post, length: int = 50) -> str:
    from bs4 import BeautifulSoup

    text = BeautifulSoup(post.body_html, "html.parser").get_text(" ", strip=True)
    text = " ".join(text.split())
    return text if len(text) <= length else text[: length - 1] + "…"


def _saved_rel(path: Path, out_dir: Path | None) -> str | None:
    """The saved file's path relative to the PDF, or None if there is none (other drive)."""
    if out_dir is None:
        return None
    try:
        return Path(os.path.relpath(path.resolve(), out_dir.resolve())).as_posix()
    except ValueError:
        return None


def _saved_href(path: Path, out_dir: Path | None) -> str:
    """Link to a saved file relative to the PDF, so the PDF and its folder can move together."""
    rel = _saved_rel(path, out_dir)
    return quote(rel) if rel is not None else path.resolve().as_uri()


def _attachment_html(att: Attachment, out_dir: Path | None = None) -> str:
    link = escape(att.full_url or att.thumb_url or "", quote=True)
    name = escape(att.filename or "file")
    details = [d for d in (att.size_text, f"{att.width}x{att.height}" if att.width else "") if d]
    label = '<div class="spoiler-label">Spoiler</div>' if att.spoiler else ""
    saved = ""
    if att.saved_path is not None:
        href = _saved_href(att.saved_path, out_dir)
        shown = _saved_rel(att.saved_path, out_dir) or str(att.saved_path.resolve())
        what = "video" if att.kind == "video" else "animation"
        saved = (
            f'<div class="saved">▶ Play {what}: <a href="{escape(href, quote=True)}">'
            f"{escape(shown)}</a></div>"
        )
    if att.local_path is None:
        return (
            f'<div class="file missing">{label}<div class="placeholder">Image unavailable: '
            f'{name}<br><a href="{link}">{link}</a><br>{escape(att.error or "")}</div>'
            f"{saved}</div>"
        )
    badge = ""
    if att.kind == "video":
        badge = '<span class="badge">▶ video</span>'
    elif att.kind == "animated":
        badge = '<span class="badge">animated</span>'
    notes = []
    if att.source == "thumb":
        notes.append('<span class="warn">thumbnail only, original unavailable</span>')
    if att.embedded_size and att.width and att.embedded_size != (att.width, att.height):
        notes.append(f"embedded at {att.embedded_size[0]}x{att.embedded_size[1]}")
    caption = f'<a href="{link}">{name}</a>'
    if details:
        caption += f" ({escape(', '.join(details))})"
    if notes:
        caption += " · " + " · ".join(notes)
    src = escape(att.local_path.resolve().as_uri(), quote=True)
    # Clicking a video or animation opens the saved file; still images open the original URL.
    target = link
    if att.saved_path is not None:
        target = escape(_saved_href(att.saved_path, out_dir), quote=True)
    return (
        f'<div class="file">{label}<a class="img-wrap" href="{target}">'
        f'<img src="{src}" alt="{name}">{badge}</a>'
        f'<div class="caption">{caption}</div>{saved}</div>'
    )


def post_html(
    post: Post, out_dir: Path | None = None, prefix: str = "", body: str | None = None
) -> str:
    bookmark = f"No.{post.id}" + (f" – {_snippet(post)}" if post.body_html else "")
    if post.is_op and post.subject:
        bookmark = f"OP: {post.subject}"
    parts = []
    if post.subject:
        parts.append(f'<span class="subject">{escape(post.subject)}</span>')
    parts.append(f'<span class="name">{escape(post.name)}</span>')
    if post.tripcode:
        parts.append(f'<span class="trip">{escape(post.tripcode)}</span>')
    if post.poster_id:
        parts.append(f'<span class="uid">(ID: {escape(post.poster_id)})</span>')
    if post.flag:
        parts.append(f'<span class="flag">[{escape(post.flag)}]</span>')
    date = post.date_text
    if not date and post.timestamp:
        date = post.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    if date:
        parts.append(f'<span class="date">{escape(date)}</span>')
    parts.append(f'<a class="num" href="#{prefix}p{post.id}">No.{post.id}</a>')
    header = " ".join(parts)
    if post.replied_by:
        links = " ".join(
            f'<a class="quotelink" href="#{prefix}p{r}">&gt;&gt;{r}</a>' for r in post.replied_by
        )
        header += f'<span class="backlinks">Replies: {links}</span>'

    files_html = "".join(_attachment_html(a, out_dir) for a in post.files)
    classes = "post op" if post.is_op else "post reply"
    return (
        f'<div class="{classes}" id="{prefix}p{post.id}">'
        f'<div class="post-header" data-bookmark="{escape(bookmark, quote=True)}">{header}</div>'
        f'{files_html}<div class="body">{post.body_html if body is None else body}</div></div>'
    )


def _title_page(thread: Thread) -> str:
    n_images = sum(len(p.files) for p in thread.posts)
    full = sum(1 for p in thread.posts for a in p.files if a.source == "full")
    first = thread.op.timestamp or next((p.timestamp for p in thread.posts if p.timestamp), None)
    last = next((p.timestamp for p in reversed(thread.posts) if p.timestamp), None)
    rows = [
        ("Board", f"/{thread.board}/" + (f" ({thread.board_title})" if thread.board_title else "")),
        ("Thread", f"No.{thread.id}"),
        (
            "Source",
            f'<a class="url" href="{escape(thread.url, quote=True)}">{escape(thread.url)}</a>',
        ),
        ("Posts", str(len(thread.posts))),
        ("Images", f"{n_images} ({full} at full resolution)"),
    ]
    if first and last:
        rows.append(("Dates", f"{first:%Y-%m-%d %H:%M} – {last:%Y-%m-%d %H:%M} UTC"))
    rows.append(("Archived", f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"))
    table = "".join(
        f"<tr><td>{k}</td><td>{v if k == 'Source' else escape(v)}</td></tr>" for k, v in rows
    )
    title = thread.subject or f"/{thread.board}/ thread {thread.id}"
    return (
        f'<section class="title-page"><h1>{escape(title)}</h1>'
        f'<div class="board">/{escape(thread.board)}/</div><table>{table}</table></section>'
    )


def thread_html(thread: Thread, title_page: bool = True, out_dir: Path | None = None) -> str:
    title = thread.subject or f"/{thread.board}/ {thread.id}"
    head = (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>'
        f'<meta name="description" content="{escape(thread.url, quote=True)}">'
        f'<meta name="generator" content="4chan2pdf"></head><body>'
    )
    body = _title_page(thread) if title_page else ""
    body += "".join(post_html(p, out_dir) for p in thread.posts)
    return head + body + "</body></html>"


def _thread_title(thread: Thread) -> str:
    return thread.subject or f"/{thread.board}/ thread {thread.id}"


def _dates(thread: Thread) -> str:
    stamps = [p.timestamp for p in thread.posts if p.timestamp]
    if not stamps:
        return ""
    return f"{min(stamps):%Y-%m-%d} – {max(stamps):%Y-%m-%d}"


THREAD_HREF_RE = re.compile(r"/(\w+)/(?:thread|res)/(\d+)[^#]*(?:#[pq]?(\d+))?")


class _Linker:
    """Rewrites a post body for a collection: prefixed anchors, links between threads."""

    def __init__(self, threads: list[Thread]):
        self.where: dict[tuple[str, int], int] = {}
        for n, t in enumerate(threads, 1):
            for p in t.posts:
                self.where.setdefault((t.board, p.id), n)

    def _other(self, soup, n: int, board: str, post_id: int, label: str):
        m = self.where.get((board, post_id))
        if m is None or m == n:
            return None
        tag = soup.new_tag(
            "a",
            attrs={
                "class": "quotelink other-thread",
                "href": f"#t{m}-p{post_id}",
                "data-thread": f"thread {m}",
            },
        )
        tag.string = label
        return tag

    def body(self, post: Post, n: int, thread: Thread) -> str:
        from bs4 import BeautifulSoup

        if "quotelink" not in post.body_html:
            return post.body_html
        soup = BeautifulSoup(post.body_html, "html.parser")
        for tag in soup.find_all(class_="quotelink"):
            classes = tag.get("class") or []
            href = tag.get("href", "")
            text = tag.get_text()
            if tag.name == "a" and href.startswith("#p"):
                tag["href"] = f"#t{n}-{href[1:]}"
            elif "external" in classes:
                m = THREAD_HREF_RE.search(href)
                if m:
                    target = int(m.group(3) or m.group(2))
                    new = self._other(soup, n, m.group(1), target, text)
                    if new is not None:
                        tag.replace_with(new)
            elif "dead" in classes and text.startswith(">>") and text[2:].isdigit():
                new = self._other(soup, n, thread.board, int(text[2:]), text)
                if new is not None:
                    tag.replace_with(new)
        return str(soup)


def _collection_title_page(threads: list[Thread], title: str) -> str:
    atts = [a for t in threads for p in t.posts for a in p.files]
    rows = [
        ("Threads", str(len(threads))),
        ("Posts", str(sum(len(t.posts) for t in threads))),
        ("Images", f"{len(atts)} ({sum(a.source == 'full' for a in atts)} at full resolution)"),
        ("Archived", f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"),
    ]
    table = "".join(f"<tr><td>{k}</td><td>{escape(v)}</td></tr>" for k, v in rows)
    return (
        f'<section class="title-page"><h1>{escape(title)}</h1>'
        f'<div class="board">{len(threads)} threads</div><table>{table}</table></section>'
    )


def _toc(threads: list[Thread]) -> str:
    items = []
    for n, t in enumerate(threads, 1):
        n_images = sum(len(p.files) for p in t.posts)
        meta = [f"/{t.board}/ No.{t.id}", f"{len(t.posts)} posts", f"{n_images} images"]
        if _dates(t):
            meta.append(_dates(t))
        items.append(
            f'<li><a href="#t{n}"><span class="toc-title">{escape(_thread_title(t))}</span></a>'
            f'<div class="toc-meta">{escape(" · ".join(meta))}</div></li>'
        )
    return f'<nav class="toc"><h2>Contents</h2><ol>{"".join(items)}</ol></nav>'


def _thread_section(thread: Thread, n: int, linker: _Linker, out_dir: Path) -> str:
    url = escape(thread.url, quote=True)
    meta = [f"/{thread.board}/ No.{thread.id}", f"{len(thread.posts)} posts"]
    if _dates(thread):
        meta.append(_dates(thread))
    posts = "".join(
        post_html(p, out_dir, prefix=f"t{n}-", body=linker.body(p, n, thread)) for p in thread.posts
    )
    return (
        f'<section class="thread" id="t{n}">'
        f'<h1 class="thread-title">{n}. {escape(_thread_title(thread))}</h1>'
        f'<div class="thread-meta">{escape(" · ".join(meta))} · '
        f'<a class="url" href="{url}">{escape(thread.url)}</a></div>{posts}</section>'
    )


def collection_html(
    threads: list[Thread], title: str, out_dir: Path, title_page: bool = True
) -> str:
    linker = _Linker(threads)
    head = (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>'
        f'<meta name="generator" content="4chan2pdf"></head><body class="collection">'
    )
    body = (_collection_title_page(threads, title) if title_page else "") + _toc(threads)
    body += "".join(_thread_section(t, n, linker, out_dir) for n, t in enumerate(threads, 1))
    return head + body + "</body></html>"


# Page styles: "plain" is thread.css alone; the others add yotsuba.css with these colours.
STYLES: dict[str, dict[str, str] | None] = {
    "plain": None,
    "yotsuba": {  # 4chan's red/beige theme (non-work-safe boards)
        "bg": "#ffffee",
        "text": "#800000",
        "link": "#0000ee",
        "title": "#af0a0f",
        "subject": "#cc1105",
        "quote": "#000080",
        "reply-bg": "#f0e0d6",
        "reply-border": "#d9bfb7",
    },
    "yotsuba-b": {  # 4chan's blue theme (work-safe boards)
        "bg": "#eef2ff",
        "text": "#000000",
        "link": "#34345c",
        "title": "#af0a0f",
        "subject": "#0f0c5d",
        "quote": "#dd0000",
        "reply-bg": "#d6daf0",
        "reply-border": "#b7c5d9",
    },
}


def _stylesheets(style: str) -> list[str]:
    sheets = [files("chan2pdf").joinpath("thread.css").read_text(encoding="utf-8")]
    colours = STYLES[style]
    if colours:
        variables = "".join(f"--{k}: {v};" for k, v in colours.items())
        sheets.append(f":root {{ {variables} }}")
        sheets.append(files("chan2pdf").joinpath("yotsuba.css").read_text(encoding="utf-8"))
    return sheets


def _render(html: str, out: Path, style: str = "plain") -> None:
    from weasyprint import CSS, HTML

    css = [CSS(string=s) for s in _stylesheets(style)]
    # No base URL: links to saved videos stay relative to the PDF. Images use absolute file URIs.
    # No dpi / jpeg_quality options: images are embedded at their native resolution.
    HTML(string=html, base_url=None).write_pdf(out, stylesheets=css, uncompressed_pdf=False)


def write_pdf(thread: Thread, out: Path, title_page: bool = True, style: str = "plain") -> None:
    _render(thread_html(thread, title_page, out.parent), out, style)


def write_collection_pdf(
    threads: list[Thread], out: Path, title: str, style: str = "plain", title_page: bool = True
) -> None:
    """Several threads in one PDF with a title page and a table of contents."""
    _render(collection_html(threads, title, out.parent, title_page), out, style)
