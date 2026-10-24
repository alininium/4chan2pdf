import shutil
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"


def _gradient(size: tuple[int, int], mode: str = "RGB") -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    img.putdata([((x * 255) // w, (y * 255) // h, 128) for y in range(h) for x in range(w)])
    return img.convert(mode)


@pytest.fixture()
def saved_thread(tmp_path: Path) -> Path:
    """The fixture page plus its images laid out like a browser's 'save page complete'.

    1472165274999.png (post 507733) is deliberately missing, only its thumbnail exists.
    """
    page = tmp_path / "thread.html"
    shutil.copy(FIXTURES / "thread_501742.html", page)
    images, thumbs = tmp_path / "images", tmp_path / "thumbs"
    images.mkdir()
    thumbs.mkdir()

    _gradient((600, 929), "RGBA").save(images / "1471994113475.png")
    _gradient((1200, 800)).save(images / "1471996600111.jpg", quality=92)
    _gradient((400, 300)).save(images / "1472165400000.png")
    shutil.copy(FIXTURES / "1472165500000.webm", images / "1472165500000.webm")
    frames = [_gradient((320, 240)), Image.new("RGB", (320, 240), "red")]
    frames[0].save(images / "1471996986000.gif", save_all=True, append_images=frames[1:])
    for stem, size in [
        ("1471994113475", (161, 250)),
        ("1471996600111", (250, 166)),
        ("1471996986000", (250, 187)),
        ("1472165274999", (250, 187)),
        ("1472165500000", (250, 187)),
    ]:
        _gradient(size).save(thumbs / f"{stem}s.jpg")
    return page
