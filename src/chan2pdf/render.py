"""Render a Thread to HTML and then to PDF with WeasyPrint."""

from __future__ import annotations

import os
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


def post_html(post: Post, out_dir: Path | None = None) -> str:
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
    parts.append(f'<a class="num" href="#p{post.id}">No.{post.id}</a>')
    header = " ".join(parts)
    if post.replied_by:
        links = " ".join(
            f'<a class="quotelink" href="#p{r}">&gt;&gt;{r}</a>' for r in post.replied_by
        )
        header += f'<span class="backlinks">Replies: {links}</span>'

    files_html = "".join(_attachment_html(a, out_dir) for a in post.files)
    classes = "post op" if post.is_op else "post reply"
    return (
        f'<div class="{classes}" id="p{post.id}">'
        f'<div class="post-header" data-bookmark="{escape(bookmark, quote=True)}">{header}</div>'
        f'{files_html}<div class="body">{post.body_html}</div></div>'
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


def write_pdf(thread: Thread, out: Path, title_page: bool = True) -> None:
    from weasyprint import CSS, HTML

    css = CSS(string=files("chan2pdf").joinpath("thread.css").read_text(encoding="utf-8"))
    # No base URL: links to saved videos stay relative to the PDF. Images use absolute file URIs.
    document = HTML(string=thread_html(thread, title_page, out.parent), base_url=None)
    # No dpi / jpeg_quality options: images are embedded at their native resolution.
    document.write_pdf(out, stylesheets=[css], uncompressed_pdf=False)
