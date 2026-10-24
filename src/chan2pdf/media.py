"""Download attachments and turn them into something the PDF can embed.

Static JPEG and PNG files are embedded byte-for-byte, so nothing is lost.
Other formats are converted losslessly to PNG; animations keep their first
frame and videos get a still frame from ffmpeg (or the board's thumbnail).
"""

from __future__ import annotations

import hashlib
import io
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from chan2pdf.fetch import Fetcher, FetchError
from chan2pdf.model import Attachment, Thread

log = logging.getLogger(__name__)
Image.MAX_IMAGE_PIXELS = 400_000_000  # /hr/ posts can be huge; still guard against bombs


def _store(data: bytes, ext: str, media_dir: Path) -> Path:
    path = media_dir / f"{hashlib.sha256(data).hexdigest()}.{ext}"
    if not path.exists():
        path.write_bytes(data)
    return path


def _to_png(img: Image.Image) -> bytes:
    if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _downscale(img: Image.Image, max_px: int) -> bytes:
    img = img.copy()
    img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    if img.mode in ("RGBA", "LA", "P"):
        return _to_png(img)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def _video_frame(data: bytes, suffix: str) -> bytes | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"in{suffix}"
        dst = Path(tmp) / "frame.png"
        src.write_bytes(data)
        try:
            subprocess.run(
                [ffmpeg, "-loglevel", "error", "-y", "-i", str(src), "-frames:v", "1", str(dst)],
                check=True,
                timeout=120,
            )
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("ffmpeg could not extract a frame: %s", e)
            return None
        return dst.read_bytes() if dst.exists() else None


def prepare_image(
    data: bytes, media_dir: Path, max_px: int | None = None
) -> tuple[Path, str, int, int]:
    """Store image bytes as an embeddable file. Returns (path, kind, width, height)."""
    with Image.open(io.BytesIO(data)) as img:
        fmt = img.format
        frames = getattr(img, "n_frames", 1)
        width, height = img.size
        kind = "animated" if frames > 1 else "image"
        if frames > 1:
            img.seek(0)
        if max_px and max(width, height) > max_px:
            out = _downscale(img, max_px)
            ext = "png" if out[:4] == b"\x89PNG" else "jpg"
            with Image.open(io.BytesIO(out)) as small:
                width, height = small.size
            return _store(out, ext, media_dir), kind, width, height
        if fmt == "JPEG":
            return _store(data, "jpg", media_dir), kind, width, height
        if fmt == "PNG" and frames == 1:
            return _store(data, "png", media_dir), kind, width, height
        return _store(_to_png(img), "png", media_dir), kind, width, height


def fetch_attachment(
    att: Attachment,
    fetcher: Fetcher,
    media_dir: Path,
    max_px: int | None = None,
    full_images: bool = True,
) -> Attachment:
    # Full file first; the thumbnail is a last resort. A spoiler's "thumbnail" is only the
    # board's placeholder, so spoilered files use the full image even with --thumbs-only.
    candidates = []
    if att.full_url and (full_images or att.spoiler):
        candidates.append(("full", att.full_url))
    if att.thumb_url:
        candidates.append(("thumb", att.thumb_url))

    errors = []
    for source, url in candidates:
        try:
            data = fetcher.get(url)
        except FetchError as e:
            errors.append(str(e))
            continue
        try:
            if source == "full" and att.is_video:
                frame = _video_frame(data, Path(url.split("?")[0]).suffix or ".webm")
                if frame is None:
                    errors.append(f"{url}: no frame extracted")
                    continue
                att.local_path, _, w, h = prepare_image(frame, media_dir, max_px)
                att.kind = "video"
            else:
                att.local_path, att.kind, w, h = prepare_image(data, media_dir, max_px)
                if source == "thumb" and att.is_video:
                    att.kind = "video"
        except (OSError, Image.DecompressionBombError, SyntaxError, ValueError) as e:
            errors.append(f"{url}: not a readable image ({e})")
            continue
        att.source = source
        att.embedded_size = (w, h)
        if source == "full" or att.width is None:
            att.width, att.height = w, h
        att.error = None
        return att
    att.source = None
    att.error = "; ".join(errors) or "no URL"
    log.warning("could not fetch %s: %s", att.filename, att.error)
    return att


def fetch_all(
    thread: Thread,
    fetcher: Fetcher,
    media_dir: Path,
    max_px: int | None = None,
    full_images: bool = True,
    workers: int = 4,
    progress: Callable[[Attachment], None] | None = None,
) -> None:
    atts = [a for p in thread.posts for a in p.files]

    def work(att: Attachment) -> Attachment:
        fetch_attachment(att, fetcher, media_dir, max_px, full_images)
        if progress:
            progress(att)
        return att

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, atts))
