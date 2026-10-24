import json
from pathlib import Path

from chan2pdf.links import rewrite_bodies
from chan2pdf.parse import api_url, parse_api, parse_html

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://suptg.thisisnotatrueending.com/qstarchive/2016/501742/"


def load():
    thread = parse_html((FIXTURES / "thread_501742.html").read_bytes(), URL)
    rewrite_bodies(thread)
    return thread


def test_thread_metadata():
    t = load()
    assert (t.board, t.id, t.subject) == ("qst", 501742, "Magical Girl For Hire Quest")
    assert [p.id for p in t.posts] == [
        501742,
        501780,
        501901,
        501933,
        501938,
        501944,
        507733,
        507740,
    ]
    op = t.op
    assert op.is_op and not t.posts[1].is_op
    assert (op.name, op.tripcode, op.poster_id) == ("Handler", "!!aGklz+NNDDf", "mqHJCNpM")
    assert op.timestamp.isoformat() == "2016-08-23T23:15:13+00:00"
    assert op.date_text == "08/23/16(Tue)19:15:13"


def test_quote_preview_copies_are_ignored():
    t = load()
    assert "preview copy" not in t.by_id()[501901].body_html


def test_full_size_url_preferred_over_thumbnail():
    att = load().op.files[0]
    assert att.full_url == URL + "images/1471994113475.png"
    assert att.thumb_url == URL + "thumbs/1471994113475s.jpg"
    assert att.filename == "magical_girl_by_resake-d8f4dz6.png"
    assert (att.width, att.height, att.size_text) == (600, 929, "227 KB")


def test_spoiler_attachment_keeps_real_file_and_name():
    att = load().by_id()[507740].files[0]
    assert att.spoiler
    assert att.filename == "secret_art.png"
    assert att.full_url.endswith("images/1472165400000.png")


def test_in_thread_quotes_become_internal_links():
    p = load().by_id()[501933]
    # The second form is a full thread URL with a fragment; both must point inside the PDF.
    assert '<a class="quotelink" href="#p501901">&gt;&gt;501901</a>' in p.body_html
    assert '<a class="quotelink" href="#p501742">&gt;&gt;501742</a>' in p.body_html
    assert p.replies_to == [501901, 501742]


def test_plain_text_quote_is_linked():
    p = load().by_id()[501938]
    assert 'href="#p501780"' in p.body_html
    assert p.replies_to == [501933, 501780]


def test_cross_thread_dead_and_board_links():
    body = load().by_id()[501944].body_html
    assert (
        '<a class="quotelink external" '
        'href="https://boards.4chan.org/qst/thread/499000#p499123">&gt;&gt;499123</a>'
    ) in body
    assert '<span class="quotelink dead">&gt;&gt;501000</span>' in body
    assert 'href="https://boards.4chan.org/tg/"' in body


def test_urls_linkified_across_wbr():
    body = load().by_id()[501944].body_html
    url = "https://www.deviantart.com/resake/art/magical-girl-d8f4dz6"
    assert f'<a class="url" href="{url}">{url}</a>.' in body


def test_markup_is_sanitized():
    t = load()
    assert '<span class="greentext">&gt;Take the job</span>' in t.by_id()[501780].body_html
    assert '<span class="spoiler">great :3</span>' in t.by_id()[501938].body_html
    for p in t.posts:
        assert "javascript:" not in p.body_html
        assert "<img" not in p.body_html


def test_backlinks():
    by_id = load().by_id()
    assert by_id[501780].replied_by == [501901, 501938, 507733, 507740]
    assert by_id[501901].replied_by == [501933]
    assert by_id[501742].replied_by == [501933]
    assert by_id[507740].replied_by == []


def test_api_parsing():
    url = "https://boards.4chan.org/qst/thread/501742/magical-girl"
    assert api_url(url) == "https://a.4cdn.org/qst/thread/501742.json"
    data = {
        "posts": [
            {
                "no": 501742,
                "sub": "Quest &amp; stuff",
                "name": "Handler",
                "time": 1471994113,
                "now": "08/23/16(Tue)19:15:13",
                "com": "Hello",
                "tim": 1471994113475,
                "ext": ".png",
                "filename": "magical_girl",
                "w": 600,
                "h": 929,
                "fsize": 232448,
            },
            {
                "no": 501780,
                "time": 1471994728,
                "com": '<a href="#p501742" class="quotelink">&gt;&gt;501742</a><br>Hi',
            },
        ]
    }
    t = parse_api(json.dumps(data), url)
    rewrite_bodies(t)
    assert t.subject == "Quest & stuff"
    att = t.op.files[0]
    assert att.full_url == "https://i.4cdn.org/qst/1471994113475.png"
    assert att.thumb_url == "https://i.4cdn.org/qst/1471994113475s.jpg"
    assert (att.filename, att.size_text) == ("magical_girl.png", "227 KB")
    assert t.op.replied_by == [501780]
