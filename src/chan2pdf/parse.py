"""Turn a thread page (or a 4chan API response) into a Thread.

The HTML parser targets 4chan's desktop markup, which is also what saved
pages and static mirrors such as the suptg quest archive contain:

    div.post#p123 > div.file > (div.fileText, a.fileThumb > img)
                  > div.postInfo.desktop > span.subject, span.nameBlock, span.dateTime
                  > blockquote.postMessage#m123

Vichan/Tinyboard style markup (div.post#reply_123, div.body) is handled on a
best-effort basis.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from chan2pdf.model import Attachment, Post, Thread

POST_ID_RE = re.compile(r"^(?:p|reply_|op_)(\d+)$")
DIMS_RE = re.compile(r"(\d+)\s*x\s*(\d+)")
SIZE_RE = re.compile(r"([\d.]+\s*[KMG]?i?B)\b", re.I)
FOURCHAN_THREAD_RE = re.compile(
    r"^https?://boards\.(?:4chan|4channel)\.org/(\w+)/thread/(\d+)", re.I
)


class ParseError(Exception):
    pass


def _classes(tag: Tag) -> list[str]:
    return tag.get("class") or []


def _text(tag: Tag | None) -> str:
    return tag.get_text(" ", strip=True) if tag else ""


def _top_level_posts(soup: BeautifulSoup) -> list[Tag]:
    """div.post elements that are not quote previews nested inside another post."""
    out = []
    seen: set[int] = set()
    for tag in soup.find_all("div", class_="post"):
        m = POST_ID_RE.match(tag.get("id", ""))
        if not m:
            continue
        if tag.find_parent("div", class_="post") is not None:
            continue
        if "inlined" in _classes(tag) or tag.find_parent(id="quote-preview"):
            continue
        pid = int(m.group(1))
        if pid in seen:
            continue
        seen.add(pid)
        out.append(tag)
    return out


def _parse_attachment(file_div: Tag, base_url: str) -> Attachment | None:
    if file_div.find(class_="fileDeletedRes") or file_div.find(class_="fileDeleted"):
        return None
    thumb_link = file_div.find("a", class_="fileThumb") or file_div.find(
        "a", href=re.compile(r"/src/")
    )
    info = file_div.find(class_="fileText") or file_div.find(class_="fileinfo")
    info_link = info.find("a") if info else None

    full = None
    if thumb_link and thumb_link.get("href"):
        full = thumb_link["href"]
    elif info_link and info_link.get("href"):
        full = info_link["href"]
    img = (thumb_link.find("img") if thumb_link else None) or file_div.find("img")
    thumb = img.get("src") if img else None
    if not full and not thumb:
        return None

    info_text = _text(info)
    filename = ""
    if info_link is not None:
        filename = info_link.get("title") or _text(info_link)
    if info is not None and not filename:
        # 4chan puts the real name of spoilered files in a title attribute.
        titled = info.find(attrs={"title": True})
        if titled is not None:
            filename = titled["title"]
    if not filename and full:
        filename = urlparse(full).path.rsplit("/", 1)[-1]

    width = height = None
    m = DIMS_RE.search(info_text)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
    size = SIZE_RE.search(info_text)
    spoiler = bool(img is not None and "spoiler" in (img.get("src", "") + " ".join(_classes(img))))

    return Attachment(
        full_url=urljoin(base_url, full) if full else None,
        thumb_url=urljoin(base_url, thumb) if thumb else None,
        filename=filename,
        width=width,
        height=height,
        size_text=size.group(1) if size else "",
        spoiler=spoiler,
        md5=img.get("data-md5") if img else None,
    )


def _parse_post(tag: Tag, base_url: str) -> Post:
    pid = int(POST_ID_RE.match(tag["id"]).group(1))
    classes = _classes(tag)
    info = tag.find(class_="postInfo", recursive=True)
    for candidate in tag.find_all(class_="postInfo"):
        if "desktop" in _classes(candidate):
            info = candidate
            break
    if info is None:
        info = tag.find(class_="intro") or tag

    post = Post(id=pid, is_op="op" in classes)
    post.subject = _text(info.find(class_="subject"))
    name = info.find(class_="name")
    if name:
        post.name = _text(name) or post.name
    post.tripcode = _text(info.find(class_="postertrip") or info.find(class_="trip"))
    uid = info.find(class_="posteruid")
    if uid:
        hand = uid.find(class_="hand")
        post.poster_id = _text(hand) if hand else re.sub(r"^\(ID:\s*|\)$", "", _text(uid))
    flag = info.find(class_="flag") or info.find(class_="countryFlag")
    if flag:
        post.flag = flag.get("title", "")

    dt = info.find(class_="dateTime") or info.find("time")
    if dt is not None:
        post.date_text = dt.get_text(" ", strip=True).split(" No.")[0]
        utc = dt.get("data-utc") or dt.get("unixtime")
        if utc and str(utc).isdigit():
            post.timestamp = datetime.fromtimestamp(int(utc), tz=timezone.utc)
        elif dt.get("datetime"):
            try:
                post.timestamp = datetime.fromisoformat(dt["datetime"].replace("Z", "+00:00"))
            except ValueError:
                pass

    for file_div in tag.find_all("div", class_=["file", "files"]):
        if file_div.find_parent("div", class_="file") is not None:
            continue
        att = _parse_attachment(file_div, base_url)
        if att:
            post.files.append(att)

    body = tag.find("blockquote", class_="postMessage") or tag.find("div", class_="body")
    if body is not None:
        post.body_html = body.decode_contents()
    return post


def _board_from(soup: BeautifulSoup, url: str) -> tuple[str, str]:
    title = _text(soup.find(class_="boardTitle"))
    m = re.match(r"/(\w+)/\s*-?\s*(.*)", title)
    if m:
        return m.group(1), title
    m = re.search(r"/(\w+)/(?:thread|res)/\d+", url)
    if m:
        return m.group(1), title
    page_title = _text(soup.find("title"))
    m = re.search(r"/(\w+)/", page_title)
    return (m.group(1) if m else "unknown"), title


def parse_html(content: bytes | str, url: str) -> Thread:
    soup = BeautifulSoup(content, "lxml")
    tags = _top_level_posts(soup)
    if not tags:
        raise ParseError("no posts found: unsupported page layout")
    posts = [_parse_post(t, url) for t in tags]
    if not any(p.is_op for p in posts):
        posts[0].is_op = True
    op = next(p for p in posts if p.is_op)
    posts.sort(key=lambda p: (not p.is_op, p.id))

    thread_div = soup.find("div", class_="thread", id=re.compile(r"^t\d+$"))
    thread_id = int(thread_div["id"][1:]) if thread_div else op.id
    board, board_title = _board_from(soup, url)
    return Thread(url=url, board=board, id=thread_id, posts=posts, board_title=board_title)


def api_url(url: str) -> str | None:
    """The 4chan JSON API URL for a live thread URL, or None."""
    m = FOURCHAN_THREAD_RE.match(url)
    if not m:
        return None
    return f"https://a.4cdn.org/{m.group(1)}/thread/{m.group(2)}.json"


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit != "MB" else f"{n:.2f} MB"
        n /= 1024
    return f"{n} B"


def parse_api(content: bytes | str, url: str) -> Thread:
    m = FOURCHAN_THREAD_RE.match(url)
    if not m:
        raise ParseError(f"not a 4chan thread URL: {url}")
    board = m.group(1)
    data = json.loads(content)
    posts = []
    for i, p in enumerate(data["posts"]):
        post = Post(
            id=p["no"],
            is_op=i == 0,
            subject=html.unescape(p.get("sub", "")),
            name=html.unescape(p.get("name", "Anonymous")),
            tripcode=p.get("trip", ""),
            poster_id=p.get("id", ""),
            flag=p.get("country_name", "") or p.get("flag_name", ""),
            timestamp=datetime.fromtimestamp(p["time"], tz=timezone.utc) if "time" in p else None,
            date_text=p.get("now", ""),
            body_html=p.get("com", ""),
        )
        if "tim" in p and not p.get("filedeleted"):
            post.files.append(
                Attachment(
                    full_url=f"https://i.4cdn.org/{board}/{p['tim']}{p['ext']}",
                    thumb_url=f"https://i.4cdn.org/{board}/{p['tim']}s.jpg",
                    filename=html.unescape(p.get("filename", str(p["tim"]))) + p["ext"],
                    width=p.get("w"),
                    height=p.get("h"),
                    size_text=_human_size(p.get("fsize", 0)),
                    spoiler=bool(p.get("spoiler")),
                    md5=p.get("md5"),
                )
            )
        posts.append(post)
    if not posts:
        raise ParseError("thread has no posts")
    return Thread(url=url, board=board, id=posts[0].id, posts=posts)
