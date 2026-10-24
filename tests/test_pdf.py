"""End to end: saved page -> PDF, then inspect the PDF with pypdf."""

from pathlib import Path

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
    assert len(titles) == 8
    assert titles[0] == "OP: Magical Girl For Hire Quest"
    assert titles[1].startswith("No.501780")


def test_thumbs_only(saved_thread, tmp_path):
    sizes = _images(_build(saved_thread, tmp_path, "--thumbs-only", "--no-title-page"))
    # Only the spoilered file is full size: its thumbnail is just the board's placeholder.
    assert ORIGINAL_SIZES & sizes == {(400, 300)}
