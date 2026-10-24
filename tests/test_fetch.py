from pathlib import Path

import httpx

from chan2pdf import fetch
from chan2pdf.fetch import Fetcher, local_path


def test_local_paths():
    assert local_path("thread.html") == Path("thread.html")
    assert local_path("C:/Users/me/thread.html") == Path("C:/Users/me/thread.html")
    assert local_path("file:///tmp/a%20b.html") == Path("/tmp/a b.html")
    assert local_path("https://example.org/x") is None


def test_retry_after_replaces_backoff(monkeypatch, tmp_path):
    sleeps = []
    monkeypatch.setattr(fetch.time, "sleep", sleeps.append)
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, content=b"ok")]
    )
    f = Fetcher(tmp_path, delay=0)
    f.client = httpx.Client(transport=httpx.MockTransport(lambda req: next(responses)))
    assert f.get("https://example.org/a.png") == b"ok"
    assert sleeps == [7]
    # Cached: no second request.
    assert f.get("https://example.org/a.png") == b"ok"


def test_gives_up_on_404_without_retrying(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)

    def handler(req):
        calls.append(req)
        return httpx.Response(404)

    f = Fetcher(None, delay=0)
    f.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        f.get("https://example.org/gone.png")
    except fetch.FetchError as e:
        assert e.status == 404
    else:
        raise AssertionError("expected FetchError")
    assert len(calls) == 1


def test_parallel_fetches_of_same_url_share_cache(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    f = Fetcher(tmp_path, delay=0)
    f.client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x"))
    )
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: f.get("https://s.4cdn.org/spoiler.png"), range(32)))
    assert results == [b"x"] * 32
    assert not list(tmp_path.rglob(".part-*"))


def test_cookies_file_keeps_httponly_cookies(tmp_path):
    from chan2pdf.cli import load_cookies

    jar = tmp_path / "cookies.txt"
    jar.write_text(
        "# Netscape HTTP Cookie File\n"
        ".4chan.org\tTRUE\t/\tTRUE\t0\tpass_enabled\t1\n"
        "#HttpOnly_.4chan.org\tTRUE\t/\tTRUE\t0\tpass_id\tsecret\n"
    )
    assert load_cookies(jar) == {"pass_enabled": "1", "pass_id": "secret"}
