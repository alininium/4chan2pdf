"""Command-line entry point: 4chan2pdf URL [-o OUT.pdf]."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
import time
from pathlib import Path

from chan2pdf import __version__
from chan2pdf.fetch import DEFAULT_UA, Fetcher, FetchError, local_path
from chan2pdf.links import rewrite_bodies
from chan2pdf.media import fetch_all
from chan2pdf.model import Thread
from chan2pdf.parse import ParseError, api_url, parse_api, parse_html
from chan2pdf.render import write_pdf

log = logging.getLogger("chan2pdf")


def default_cache_dir() -> Path:
    return Path.home() / ".cache" / "4chan2pdf"


def slugify(text: str, length: int = 40) -> str:
    slug = re.sub(r"[^\w]+", "-", text.lower()).strip("-")
    return slug[:length].rstrip("-")


def default_output(thread: Thread) -> Path:
    name = f"{thread.board}_{thread.id}"
    if thread.subject:
        name += "_" + slugify(thread.subject)
    return Path(f"{name}.pdf")


def load_cookies(path: Path | None) -> dict[str, str] | None:
    """Read a Netscape cookies.txt file."""
    if not path:
        return None
    cookies = {}
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            cookies[fields[5]] = fields[6]
    return cookies


def load_thread(url: str, fetcher: Fetcher) -> Thread:
    path = local_path(url)
    if path is not None:
        url = path.resolve().as_uri()
    api = api_url(url)
    if api:
        log.info("using the 4chan JSON API: %s", api)
        thread = parse_api(fetcher.get(api, use_cache=False), url)
    else:
        thread = parse_html(fetcher.get(url, use_cache=False), url)
    rewrite_bodies(thread)
    return thread


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="4chan2pdf",
        description="Save an imageboard thread as a PDF with full-resolution images "
        "and clickable links between posts.",
    )
    p.add_argument("url", help="thread URL, or a saved .html file")
    p.add_argument("-o", "--output", type=Path, help="output PDF (default: board_id_subject.pdf)")
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_dir(),
        help="download cache (default: %(default)s)",
    )
    p.add_argument("--no-cache", action="store_true", help="do not cache downloads on disk")
    p.add_argument(
        "--max-image-px",
        type=int,
        metavar="N",
        help="downscale images whose longest side exceeds N pixels (default: never)",
    )
    p.add_argument(
        "--thumbs-only",
        action="store_true",
        help="use thumbnails instead of full-size files (small PDF)",
    )
    p.add_argument("--no-title-page", action="store_true", help="omit the title page")
    p.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="seconds between HTTP requests (default: %(default)s)",
    )
    p.add_argument("--workers", type=int, default=4, help="parallel image downloads")
    p.add_argument("--user-agent", default=DEFAULT_UA)
    p.add_argument("--cookies", type=Path, help="Netscape cookies.txt file")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.ERROR)

    started = time.monotonic()
    cache = None if args.no_cache else args.cache_dir
    try:
        with Fetcher(cache, args.delay, args.user_agent, load_cookies(args.cookies)) as fetcher:
            thread = load_thread(args.url, fetcher)
            n_files = sum(len(p.files) for p in thread.posts)
            log.info(
                "/%s/ %s: %d posts, %d files", thread.board, thread.id, len(thread.posts), n_files
            )

            done = 0

            def progress(_att: object) -> None:
                nonlocal done
                done += 1
                print(f"\rimages {done}/{n_files}", end="", file=sys.stderr, flush=True)

            with tempfile.TemporaryDirectory(prefix="4chan2pdf-") as media_dir:
                fetch_all(
                    thread,
                    fetcher,
                    Path(media_dir),
                    args.max_image_px,
                    not args.thumbs_only,
                    args.workers,
                    progress,
                )
                if n_files:
                    print(file=sys.stderr)
                out = args.output or default_output(thread)
                log.info("rendering %s", out)
                write_pdf(thread, out, title_page=not args.no_title_page)
    except FetchError as e:
        log.error("download failed: %s", e)
        if e.status == 404:
            log.error("the thread may have been pruned or deleted")
        return 2
    except ParseError as e:
        log.error("could not read the thread: %s", e)
        return 3

    atts = [a for p in thread.posts for a in p.files]
    internal = sum(len(p.replies_to) for p in thread.posts)
    size_mb = out.stat().st_size / 1e6
    print(
        f"wrote {out} ({size_mb:.1f} MB) in {time.monotonic() - started:.0f}s\n"
        f"  posts: {len(thread.posts)}\n"
        f"  images: {len(atts)} total, "
        f"{sum(a.source == 'full' for a in atts)} full-size, "
        f"{sum(a.source == 'thumb' for a in atts)} thumbnail only, "
        f"{sum(a.source is None for a in atts)} failed\n"
        f"  links between posts: {internal}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
