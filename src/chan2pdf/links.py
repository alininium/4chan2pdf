"""Clean post markup and wire up links between posts.

After rewrite_bodies() every post body contains only a small set of tags, and
every quote is one of:

    <a class="quotelink" href="#p123">            post in this thread
    <a class="quotelink external" href="https://…">  post in another thread or board
    <span class="quotelink dead">                 post that is not in the thread
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

from chan2pdf.model import Thread

KEEP_TAGS = {"br", "b", "i", "u", "strong", "em", "pre"}
QUOTE_RE = re.compile(r"^>>(\d+)$")
CROSS_RE = re.compile(r"^>>>/(\w+)/(\d+)?/?$")
HREF_POST_RE = re.compile(r"#[pq]?(\d+)$")
HREF_THREAD_RE = re.compile(r"/(?:thread|res)/(\d+)")
INLINE_RE = re.compile(r"(>>>/\w+/(?:\d+)?|>>\d+|https?://[^\s<>\"']+)")


class _Ctx:
    def __init__(self, thread: Thread):
        self.thread = thread
        self.ids = {p.id for p in thread.posts}
        self.base = thread.url
        self.board = thread.board

    def external(self, href: str) -> str:
        if href.startswith("//"):
            return "https:" + href
        if urlparse(href).scheme:
            return href
        # Root-relative board links (/qst/thread/123) only make sense on the board itself.
        # From a saved file or a static mirror, point them at the original site instead.
        if href.startswith("/") and not HREF_THREAD_RE.search(urlparse(self.base).path):
            return urljoin("https://boards.4chan.org/", href)
        return urljoin(self.base, href)

    def cross_board(self, board: str, post: str | None) -> str:
        if post:
            return f"https://boards.4chan.org/{board}/thread/{post}#p{post}"
        return f"https://boards.4chan.org/{board}/"


def _new(soup: BeautifulSoup, name: str, text: str, **attrs: str) -> Tag:
    tag = soup.new_tag(name, attrs=attrs)
    tag.string = text
    return tag


def _quote_tag(soup: BeautifulSoup, ctx: _Ctx, text: str, href: str, refs: list[int]) -> Tag:
    """Build the right link for a quote whose visible text is `text`."""
    m = CROSS_RE.match(text)
    if m:
        return _new(
            soup,
            "a",
            text,
            href=ctx.cross_board(m.group(1), m.group(2)),
            **{"class": "quotelink external"},
        )
    m = QUOTE_RE.match(text) or HREF_POST_RE.search(href)
    num = int(m.group(1)) if m else None
    if num is not None and num in ctx.ids:
        refs.append(num)
        return _new(soup, "a", f">>{num}", href=f"#p{num}", **{"class": "quotelink"})
    tm = HREF_THREAD_RE.search(href)
    if href and tm and int(tm.group(1)) != ctx.thread.id:
        label = text if text.startswith(">>") else f">>{num or tm.group(1)}"
        return _new(soup, "a", label, href=ctx.external(href), **{"class": "quotelink external"})
    label = f">>{num}" if num is not None else text
    return _new(soup, "span", label, **{"class": "quotelink dead"})


def _linkify(soup: BeautifulSoup, ctx: _Ctx, refs: list[int]) -> None:
    for node in list(soup.find_all(string=INLINE_RE)):
        if node.find_parent(["a", "pre"]) is not None or node.find_parent(class_="quotelink"):
            continue
        parts = INLINE_RE.split(str(node))
        new_nodes: list[NavigableString | Tag] = []
        for i, part in enumerate(parts):
            if not part:
                continue
            if i % 2 == 0:
                new_nodes.append(NavigableString(part))
            elif part.startswith(">>"):
                new_nodes.append(_quote_tag(soup, ctx, part, "", refs))
            else:
                url = part.rstrip(".,;:!?)")
                tail = part[len(url) :]
                new_nodes.append(_new(soup, "a", url, href=url, **{"class": "url"}))
                if tail:
                    new_nodes.append(NavigableString(tail))
        for n in new_nodes:
            node.insert_before(n)
        node.extract()


def clean_body(body_html: str, ctx: _Ctx) -> tuple[str, list[int]]:
    soup = BeautifulSoup(body_html, "html.parser")
    refs: list[int] = []

    for tag in soup.find_all(["script", "style", "img", "input", "iframe", "object"]):
        tag.decompose()
    for tag in soup.find_all(class_="abbr"):  # "Comment too long. Click here…"
        tag.decompose()
    for tag in soup.find_all("wbr"):
        tag.unwrap()
    soup.smooth()

    for tag in list(soup.find_all(True)):
        if tag.parent is None:  # already replaced together with an ancestor
            continue
        classes = tag.get("class") or []
        text = tag.get_text()
        if tag.name == "a":
            href = tag.get("href", "")
            if "quotelink" in classes or text.startswith(">>") or HREF_POST_RE.search(href):
                tag.replace_with(_quote_tag(soup, ctx, text.strip(), href, refs))
            elif href.startswith(("http://", "https://", "//")):
                tag.attrs = {"href": ctx.external(href), "class": "url"}
            else:
                tag.unwrap()
        elif "deadlink" in classes:
            tag.replace_with(_quote_tag(soup, ctx, text.strip(), "", refs))
        elif tag.name == "span" and "quote" in classes:
            tag.attrs = {"class": "greentext"}
        elif tag.name in ("s", "del") or "spoiler" in classes:
            tag.name = "span"
            tag.attrs = {"class": "spoiler"}
        elif tag.name == "code" or "prettyprint" in classes:
            tag.name = "pre"
            tag.attrs = {"class": "code"}
        elif tag.name in KEEP_TAGS:
            tag.attrs = {}
        else:
            tag.unwrap()

    soup.smooth()
    _linkify(soup, ctx, refs)
    return str(soup), refs


def rewrite_bodies(thread: Thread) -> None:
    """Clean every post body in place and fill replies_to / replied_by."""
    ctx = _Ctx(thread)
    by_id = thread.by_id()
    for post in thread.posts:
        post.body_html, refs = clean_body(post.body_html, ctx)
        post.replies_to = list(dict.fromkeys(refs))
        post.replied_by = []
    for post in thread.posts:
        for target in post.replies_to:
            if target != post.id and post.id not in by_id[target].replied_by:
                by_id[target].replied_by.append(post.id)
