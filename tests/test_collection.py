"""Several threads in one PDF, and re-runs that reuse what was already downloaded."""

import shutil
from pathlib import Path

import pytest
from pypdf import PdfReader

from chan2pdf.cli import main
from chan2pdf.store import Store


def _run(*args: str) -> int:
    return main([*args, "--no-cache", "--delay", "0"])


def _links_file(tmp_path: Path, *pages: Path) -> Path:
    links = tmp_path / "quest.txt"
    links.write_text("# my quests\n\n" + "".join(f"{p}\n" for p in pages))
    return links


def _outline(reader: PdfReader) -> list:
    def walk(items):
        out = []
        for item in items:
            if isinstance(item, list):
                out.append(walk(item))
            else:
                out.append(item.title)
        return out

    return walk(reader.outline)


def _internal_links(reader: PdfReader) -> list[str]:
    dests = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            if annot.get("/Subtype") == "/Link" and "/Dest" in annot:
                dests.append(str(annot["/Dest"]))
    return dests


def test_collection_from_links_file(saved_thread, second_thread, tmp_path):
    links = _links_file(tmp_path, saved_thread, second_thread)
    assert _run("-i", str(links)) == 0
    reader = PdfReader(tmp_path / "quest.pdf")

    outline = _outline(reader)
    assert outline[0] == "1. Magical Girl For Hire Quest"
    assert len(outline[1]) == 9  # posts of thread 1, nested under it
    assert outline[2] == "2. Magical Girl For Hire Quest #2"
    assert outline[3] == [
        "OP: Magical Girl For Hire Quest #2",
        "No.513210 – >>513200 Haggle again, like >>501901 said.",
    ]

    named = set(reader.named_destinations)
    assert {"t1", "t2", "t1-p501742", "t2-p513200", "t2-p513210"} <= named
    links_ = _internal_links(reader)
    assert set(links_) <= named
    # In-thread quotes stay in their own thread...
    assert "t2-p513200" in links_
    # ...and quotes of the earlier thread jump into it, including a quote 4chan marked dead.
    assert {"t1-p501742", "t1-p501901", "t1-p507733"} <= set(links_)

    text = reader.pages[1].extract_text()
    assert "Contents" in text
    assert "Magical Girl For Hire Quest #2" in text


def test_adding_links_does_not_download_old_threads(saved_thread, second_thread, tmp_path):
    links = _links_file(tmp_path, saved_thread)
    assert _run("-i", str(links)) == 0
    store = Store(tmp_path / "quest_files")
    assert list(store.index["threads"]) == [saved_thread.resolve().as_uri()]

    # Remove the first thread's source page and images: a re-run must not need them.
    saved_thread.rename(saved_thread.with_suffix(".gone"))
    shutil.move(tmp_path / "images", tmp_path / "images_gone")
    (tmp_path / "images").mkdir()
    shutil.copy(tmp_path / "images_gone" / "1472330000000.jpg", tmp_path / "images")

    links.write_text(links.read_text() + f"{second_thread}\n")
    assert _run("-i", str(links)) == 0
    reader = PdfReader(tmp_path / "quest.pdf")
    sizes = {img.image.size for page in reader.pages for img in page.images}
    assert {(600, 929), (1200, 800), (900, 600)} <= sizes  # old images from the work folder
    assert len(Store(tmp_path / "quest_files").index["threads"]) == 2


def test_refresh_picks_up_new_posts_and_keeps_images(saved_thread, tmp_path):
    out = tmp_path / "one.pdf"
    assert _run(str(saved_thread), "-o", str(out)) == 0

    html = saved_thread.read_text()
    new_post = (
        '<div class="postContainer replyContainer" id="pc507800"><div id="p507800" '
        'class="post reply"><blockquote class="postMessage">'
        '<a href="#p501742" class="quotelink">&gt;&gt;501742</a> new post</blockquote>'
        "</div></div></div></div></form>"
    )
    saved_thread.write_text(html.replace("</div></div></form>", new_post, 1))
    shutil.rmtree(tmp_path / "images")  # images must come from the work folder now

    assert _run(str(saved_thread), "-o", str(out)) == 0
    assert "new post" not in "".join(p.extract_text() for p in PdfReader(out).pages)

    assert _run(str(saved_thread), "-o", str(out), "--refresh") == 0
    reader = PdfReader(out)
    assert "new post" in "".join(p.extract_text() for p in reader.pages)
    sizes = {img.image.size for page in reader.pages for img in page.images}
    assert {(600, 929), (1200, 800)} <= sizes


def test_failed_link_still_renders_the_rest(saved_thread, tmp_path):
    links = _links_file(tmp_path, saved_thread, tmp_path / "missing.html")
    assert _run("-i", str(links)) == 1
    assert (tmp_path / "quest.pdf").exists()


def test_changed_image_settings_reprepare_saved_threads(saved_thread, tmp_path):
    links = _links_file(tmp_path, saved_thread)
    assert _run("-i", str(links)) == 0
    assert _run("-i", str(links), "--max-image-px", "300") == 0
    reader = PdfReader(tmp_path / "quest.pdf")
    sizes = {img.image.size for page in reader.pages for img in page.images}
    assert max(max(s) for s in sizes) <= 300


def test_several_urls_need_an_output_name(saved_thread, second_thread):
    with pytest.raises(SystemExit) as e:
        _run(str(saved_thread), str(second_thread))
    assert e.value.code == 2


def test_store_round_trip(saved_thread, tmp_path):
    from chan2pdf.cli import load_thread
    from chan2pdf.fetch import Fetcher

    with Fetcher(None, delay=0) as f:
        thread = load_thread(str(saved_thread), f)
    store = Store(tmp_path / "work")
    store.save("key", thread)
    again = Store(tmp_path / "work").load("key")
    assert again == thread


def test_refresh_of_vanished_thread_keeps_saved_copy(saved_thread, second_thread, tmp_path):
    links = _links_file(tmp_path, saved_thread, second_thread)
    assert _run("-i", str(links)) == 0
    second_thread.unlink()  # the thread 404s now
    assert _run("-i", str(links), "--refresh", "--no-title-page") == 0
    reader = PdfReader(tmp_path / "quest.pdf")
    assert _outline(reader)[2] == "2. Magical Girl For Hire Quest #2"
    assert "Contents" in reader.pages[0].extract_text()  # no title page in front
