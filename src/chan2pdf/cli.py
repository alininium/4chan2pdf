"""Command-line entry point.

    4chan2pdf URL [URL…] [-i links.txt] [-o OUT.pdf]

Every thread is kept in a work folder (default `<output>_files/`), so running
again with more links only downloads the new ones.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

from chan2pdf import __version__
from chan2pdf.fetch import DEFAULT_UA, Fetcher, FetchError, local_path
from chan2pdf.links import rewrite_bodies
from chan2pdf.media import fetch_all
from chan2pdf.model import Thread
from chan2pdf.parse import ParseError, api_url, parse_api, parse_html
from chan2pdf.render import STYLES, write_collection_pdf, write_pdf
from chan2pdf.store import Store, carry_over, reset_attachment

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
        # Browsers export HttpOnly (often the session) cookies as "#HttpOnly_<domain>\t…".
        line = line.removeprefix("#HttpOnly_")
        if line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) >= 7:
            cookies[fields[5]] = fields[6]
    return cookies


def normalize_url(url: str) -> str:
    """The key a thread is stored under: file URI for local paths, no #fragment."""
    url = url.strip()
    path = local_path(url)
    if path is not None:
        return path.resolve().as_uri()
    return url.split("#", 1)[0]


def read_links(path: Path) -> list[str]:
    """One link per line; blank lines and lines starting with # are ignored."""
    links = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            links.append(line)
    return links


def load_thread(url: str, fetcher: Fetcher) -> Thread:
    url = normalize_url(url)
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
        description="Save imageboard threads as a PDF with full-resolution images "
        "and clickable links between posts. Several threads go into one PDF with a "
        "table of contents.",
    )
    p.add_argument("urls", nargs="*", metavar="URL", help="thread URLs or saved .html files")
    p.add_argument(
        "-i",
        "--input",
        type=Path,
        metavar="FILE",
        help="file with one thread URL per line (# starts a comment)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output PDF (default: the input file's name, or board_id_subject.pdf "
        "for a single thread)",
    )
    p.add_argument("--title", help="title of a multi-thread PDF (default: from the file name)")
    p.add_argument(
        "--workdir",
        type=Path,
        help="where downloaded threads and images are kept between runs "
        "(default: <output name>_files next to the PDF)",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="download the thread pages again (new posts) and retry failed images; "
        "images already downloaded are reused",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=default_cache_dir(),
        help="HTTP download cache (default: %(default)s)",
    )
    p.add_argument("--no-cache", action="store_true", help="do not use the HTTP cache")
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
        "--style",
        choices=sorted(STYLES),
        default="plain",
        help="page look: plain (default), yotsuba (4chan's beige/red theme) or "
        "yotsuba-b (4chan's blue theme)",
    )
    p.add_argument(
        "--media-dir",
        type=Path,
        help="where videos and animated GIFs are saved for the PDF to link to "
        "(default: <workdir>/media)",
    )
    p.add_argument(
        "--no-save-media",
        action="store_true",
        help="do not save videos and animations; link to the original URLs",
    )
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


def _progress(label: str, total: int):
    done = 0

    def tick(_att: object) -> None:
        nonlocal done
        done += 1
        print(f"\r  {label}: images {done}/{total}", end="", file=sys.stderr, flush=True)

    return tick


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("fontTools").setLevel(logging.WARNING)
    logging.getLogger("weasyprint").setLevel(logging.ERROR)

    urls = list(args.urls)
    if args.input:
        try:
            urls += read_links(args.input)
        except OSError as e:
            parser.error(f"cannot read {args.input}: {e}")
    urls = list(dict.fromkeys(normalize_url(u) for u in urls))  # keep order, drop repeats
    if not urls:
        parser.error("give at least one URL or --input FILE")
    collection = len(urls) > 1 or args.input is not None
    if collection and not (args.output or args.input):
        parser.error("several URLs need -o OUT.pdf (or put them in a file and use -i)")

    started = time.monotonic()
    cache = None if args.no_cache else args.cache_dir
    threads: list[Thread] = []
    failed: list[tuple[str, str]] = []
    out: Path | None = args.output or (args.input.with_suffix(".pdf") if args.input else None)
    store: Store | None = None
    reprepare = False  # image settings changed since the saved threads were prepared

    def open_store(out: Path) -> Store:
        nonlocal reprepare
        store = Store(args.workdir or out.parent / f"{out.stem}_files")
        settings = {"max_image_px": args.max_image_px, "thumbs_only": args.thumbs_only}
        if store.settings_changed(settings):
            log.info("image settings changed: saved threads' images will be prepared again")
            reprepare = True
        return store

    if out is not None:
        store = open_store(out)

    with Fetcher(cache, args.delay, args.user_agent, load_cookies(args.cookies)) as fetcher:
        for n, url in enumerate(urls, 1):
            label = f"[{n}/{len(urls)}]"
            saved = store.load(url) if store is not None else None
            if saved is not None and not args.refresh:
                thread, state = saved, "saved copy"
            else:
                try:
                    thread = load_thread(url, fetcher)
                except (FetchError, ParseError) as e:
                    if saved is not None:  # --refresh of a thread that is gone: keep our copy
                        log.warning("%s %s: %s; using the saved copy", label, url, e)
                        thread, state = saved, "saved copy"
                    else:
                        log.error("%s %s: %s", label, url, e)
                        if isinstance(e, FetchError) and e.status == 404:
                            log.error("the thread may have been pruned or deleted")
                        failed.append((url, str(e)))
                        continue
                else:
                    if store is None:
                        # A single thread without -o is named after its subject.
                        out = default_output(thread)
                        store = open_store(out)
                        saved = store.load(url)
                    # Keep images that are already downloaded; failed ones are tried again.
                    carry_over(None if reprepare else saved, thread)
                    state = "downloaded"
            if state == "saved copy" and reprepare:
                for att in (a for p in thread.posts for a in p.files):
                    reset_attachment(att)

            n_files = sum(len(p.files) for p in thread.posts)
            log.info(
                "%s /%s/ %s (%s): %d posts, %d files",
                label,
                thread.board,
                thread.id,
                state,
                len(thread.posts),
                n_files,
            )
            save_dir = None if args.no_save_media else (args.media_dir or store.media)
            attempted = fetch_all(
                thread,
                fetcher,
                store.images,
                args.max_image_px,
                not args.thumbs_only,
                args.workers,
                _progress(label, n_files),
                save_dir,
                retry_failed=state == "downloaded",
            )
            if attempted:
                print(file=sys.stderr)
            store.save(url, thread)
            threads.append(thread)

    if not threads:
        log.error("nothing to render: no thread could be loaded")
        return 2

    assert out is not None
    log.info("rendering %s", out)
    if collection:
        title = args.title or (args.input.stem if args.input else out.stem)
        write_collection_pdf(threads, out, title, args.style, not args.no_title_page)
    else:
        write_pdf(threads[0], out, title_page=not args.no_title_page, style=args.style)

    atts = [a for t in threads for p in t.posts for a in p.files]
    internal = sum(len(p.replies_to) for t in threads for p in t.posts)
    size_mb = out.stat().st_size / 1e6
    print(
        f"wrote {out} ({size_mb:.1f} MB) in {time.monotonic() - started:.0f}s\n"
        f"  threads: {len(threads)}" + (f", {len(failed)} failed" if failed else "") + "\n"
        f"  posts: {sum(len(t.posts) for t in threads)}\n"
        f"  images: {len(atts)} total, "
        f"{sum(a.source == 'full' for a in atts)} full-size, "
        f"{sum(a.source == 'thumb' for a in atts)} thumbnail only, "
        f"{sum(a.source is None for a in atts)} failed\n"
        f"  videos/animations saved: {sum(a.saved_path is not None for a in atts)}\n"
        f"  links between posts: {internal}\n"
        f"  saved for next time in: {store.root}",
        file=sys.stderr,
    )
    for url, err in failed:
        print(f"  failed: {url}: {err}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
