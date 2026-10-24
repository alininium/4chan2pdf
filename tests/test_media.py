import io

from PIL import Image

from chan2pdf.fetch import Fetcher
from chan2pdf.media import fetch_attachment, prepare_image
from chan2pdf.model import Attachment


def _png(size, mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, "blue").save(buf, "PNG")
    return buf.getvalue()


def test_jpeg_and_png_embedded_byte_for_byte(tmp_path):
    png = _png((50, 40))
    path, kind, w, h = prepare_image(png, tmp_path)
    assert path.read_bytes() == png and (kind, w, h) == ("image", 50, 40)
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), "red").save(buf, "JPEG")
    path, _, _, _ = prepare_image(buf.getvalue(), tmp_path)
    assert path.suffix == ".jpg" and path.read_bytes() == buf.getvalue()


def test_webp_converted_losslessly(tmp_path):
    src = Image.new("RGB", (64, 48))
    src.putdata([(x * 4, y * 5, 7) for y in range(48) for x in range(64)])
    buf = io.BytesIO()
    src.save(buf, "WEBP", lossless=True)
    path, kind, w, h = prepare_image(buf.getvalue(), tmp_path)
    assert path.suffix == ".png" and (w, h) == (64, 48)
    with Image.open(path) as out:
        assert out.convert("RGB").tobytes() == src.tobytes()


def test_animated_gif_first_frame(tmp_path):
    frames = [Image.new("RGB", (20, 10), c) for c in ("green", "red")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    path, kind, _, _ = prepare_image(buf.getvalue(), tmp_path)
    assert kind == "animated"
    with Image.open(path) as out:
        assert out.convert("RGB").getpixel((0, 0)) == (0, 128, 0)


def test_max_px_downscales(tmp_path):
    path, _, w, h = prepare_image(_png((2000, 1000)), tmp_path, max_px=500)
    assert (w, h) == (500, 250)


def test_duplicates_share_one_file(tmp_path):
    png = _png((10, 10))
    assert prepare_image(png, tmp_path)[0] == prepare_image(png, tmp_path)[0]


def test_falls_back_to_thumbnail(tmp_path):
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (25, 20)).save(thumb)
    att = Attachment(
        full_url=str(tmp_path / "missing.png"), thumb_url=str(thumb), width=800, height=640
    )
    media = tmp_path / "media"
    media.mkdir()
    with Fetcher(None, delay=0) as f:
        fetch_attachment(att, f, media)
    assert att.source == "thumb"
    assert att.embedded_size == (25, 20)
    assert (att.width, att.height) == (800, 640)  # original dimensions kept for the caption


def test_records_failure(tmp_path):
    att = Attachment(full_url=str(tmp_path / "a.png"), thumb_url=str(tmp_path / "b.jpg"))
    with Fetcher(None, delay=0) as f:
        fetch_attachment(att, f, tmp_path)
    assert att.source is None and att.local_path is None and "a.png" in att.error
