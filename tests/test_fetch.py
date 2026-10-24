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
