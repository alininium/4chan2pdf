"""End to end: saved page -> PDF, then inspect the PDF with pypdf."""

import shutil
from pathlib import Path

import pytest
from pypdf import PdfReader

from chan2pdf.cli import main

ORIGINAL_SIZES = {(600, 929), (1200, 800), (320, 240), (400, 300)}


def _build(saved_thread: Path, tmp_path: Path, *args: str) -> PdfReader:
    out = tmp_path / "out.pdf"
    code = main([str(saved_thread), "-o", str(out), "--no-cache", "--delay", "0", *args])
    assert code == 0
    return PdfReader(out)


def _images(reader: PdfReader) -> set[tuple[int, int]]:
    sizes = set()
    for page in reader.pages:
        for img in page.images:
            sizes.add(img.image.size)
    return sizes


def _internal_links(reader: PdfReader) -> list[str]:
    dests = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            if annot.get("/Subtype") == "/Link" and "/Dest" in annot:
                dests.append(str(annot["/Dest"]))
    return dests


def _uri_links(reader: PdfReader) -> set[str]:
    uris = set()
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            action = annot.get_object().get("/A")
            if action is not None and "/URI" in action:
                uris.add(str(action["/URI"]))
    return uris


def _outline_titles(reader: PdfReader) -> list[str]:
    titles = []

    def walk(items):
        for item in items:
            if isinstance(item, list):
                walk(item)
            else:
                titles.append(item.title)

    walk(reader.outline)
    return titles


def test_images_embedded_at_original_resolution(saved_thread, tmp_path):
    sizes = _images(_build(saved_thread, tmp_path))
    assert ORIGINAL_SIZES <= sizes
    assert (250, 187) in sizes  # thumbnail for the post whose original is missing


def test_post_links_resolve_to_named_destinations(saved_thread, tmp_path):
    reader = _build(saved_thread, tmp_path)
    named = set(reader.named_destinations)
    assert {f"p{n}" for n in (501742, 501780, 501901, 507740)} <= named
    links = _internal_links(reader)
    assert links and set(links) <= named
    assert "p501780" in links and "p501901" in links


def test_outline_has_one_entry_per_post(saved_thread, tmp_path):
    titles = _outline_titles(_build(saved_thread, tmp_path))
    assert len(titles) == 9
    assert titles[0] == "OP: Magical Girl For Hire Quest"
    assert titles[1].startswith("No.501780")


def test_thumbs_only(saved_thread, tmp_path):
    sizes = _images(_build(saved_thread, tmp_path, "--thumbs-only", "--no-title-page"))
    # Only the spoilered file is full size: its thumbnail is just the board's placeholder.
    assert ORIGINAL_SIZES & sizes == {(400, 300)}


def test_videos_and_animations_saved_and_linked_relatively(saved_thread, tmp_path):
    reader = _build(saved_thread, tmp_path)
    media = tmp_path / "out_media"
    gif = media / "1471996986000_dance.gif"
    webm = media / "1472165500000_transformation_sequence.webm"
    assert gif.read_bytes() == (saved_thread.parent / "images/1471996986000.gif").read_bytes()
    assert webm.read_bytes() == (saved_thread.parent / "images/1472165500000.webm").read_bytes()
    assert sorted(p.name for p in media.iterdir()) == [gif.name, webm.name]  # stills not saved
    uris = _uri_links(reader)
    assert {f"out_media/{gif.name}", f"out_media/{webm.name}"} <= uris
    text = "".join(page.extract_text() for page in reader.pages)
    assert "Play animation" in text and "Play video" in text


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_video_first_frame_embedded(saved_thread, tmp_path):
    assert (480, 360) in _images(_build(saved_thread, tmp_path))


def test_no_save_media(saved_thread, tmp_path):
    reader = _build(saved_thread, tmp_path, "--no-save-media")
    assert not (tmp_path / "out_media").exists()
    assert not any(u.startswith("out_media/") for u in _uri_links(reader))


def test_spoiler_label_before_image(saved_thread, tmp_path):
    reader = _build(saved_thread, tmp_path, "--no-title-page")
    text = "".join(page.extract_text() for page in reader.pages)
    assert text.count("SPOILER") == 1
    assert text.index("SPOILER") < text.index("secret_art.png")
