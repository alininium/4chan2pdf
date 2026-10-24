# 4chan2pdf

Save an imageboard thread as a single PDF that keeps:

- **every image at the best resolution available.** It downloads the full-size file the
  thumbnail links to and embeds JPEG and PNG files byte-for-byte. Other formats are converted
  to PNG losslessly. It only falls back to the thumbnail when the original is gone, and the
  caption says so.
- **the links between posts.** Each post is a PDF destination, every `>>123` quote jumps to
  that post, each post lists its replies (backlinks), and the PDF outline has one bookmark
  per post. Quotes to other threads stay as web links marked *(external)*. Quotes to posts
  that are not in the thread are struck through and marked *(dead)*.

Videos (webm/mp4) and animated GIFs show their first frame in the PDF. The original files
are saved in the work folder next to the PDF (`quest.pdf` → `quest_files/media/`), and
clicking the picture or the "▶ Play video / animation" line opens them. These links are
relative, so you can move or copy the PDF as long as its `_files` folder goes with it.

Spoilered images are downloaded unspoilered, with a **SPOILER** label above them.

It works with:

| Source | How |
| --- | --- |
| Live 4chan threads (`boards.4chan.org/<board>/thread/<no>`) | 4chan JSON API |
| Static 4chan-markup mirrors, e.g. the [suptg quest archive](https://suptg.thisisnotatrueending.com/qstarchive/) | HTML parser |
| A page saved with the browser's *Save page as… → Web page, complete* | HTML parser, fully offline |
| vichan / Tinyboard boards | HTML parser, best effort |

## Install

WeasyPrint needs Pango. On Debian/Ubuntu run `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`.
On macOS run `brew install pango`. See the
[WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html) for other
systems. `ffmpeg` is optional and is used to take a still frame from webm/mp4 files.

```sh
pip install git+https://github.com/alininium/4chan2pdf
# or, from a checkout:
pip install -e '.[dev]'
```

### Gentoo

The repository is also a small overlay with a live ebuild,
`gentoo/app-text/4chan2pdf/4chan2pdf-9999.ebuild`, which builds the latest code from git:

```sh
git clone https://github.com/alininium/4chan2pdf.git ~/src/4chan2pdf
printf '[4chan2pdf]\nlocation = %s\nauto-sync = no\n' "$HOME/src/4chan2pdf/gentoo" \
    | sudo tee /etc/portage/repos.conf/4chan2pdf.conf
echo 'app-text/4chan2pdf **' | sudo tee -a /etc/portage/package.accept_keywords/4chan2pdf
sudo emerge --ask app-text/4chan2pdf
```

To update, run `git pull` in the clone and `sudo emerge --ask app-text/4chan2pdf` again. The
ebuild doesn't require `media-video/ffmpeg` (video frames) or `dev-python/pillow[webp]`
(WebP images); emerge prints a reminder about both after installing. You can also copy the
ebuild into a local overlay you already have.

Without the ebuild, install the system libraries, a font and (optionally) ffmpeg:

```sh
sudo emerge --ask x11-libs/pango media-fonts/dejavu media-video/ffmpeg
```

Gentoo marks the system Python as externally managed, so a plain `pip install` is refused.
Install with `pipx` instead, which gives the tool its own virtualenv:

```sh
sudo emerge --ask dev-python/pipx
pipx install git+https://github.com/alininium/4chan2pdf
```

Or use a virtualenv yourself:

```sh
python -m venv ~/.venvs/4chan2pdf
~/.venvs/4chan2pdf/bin/pip install git+https://github.com/alininium/4chan2pdf
~/.venvs/4chan2pdf/bin/4chan2pdf --help
```

ffmpeg's built-in decoders handle webm and mp4, so no extra USE flags are needed.

## Usage

```sh
4chan2pdf https://suptg.thisisnotatrueending.com/qstarchive/2016/501742/
4chan2pdf https://boards.4chan.org/qst/thread/501742 -o quest.pdf
4chan2pdf ~/Downloads/thread.html            # saved page, images next to it
```

| Option | Meaning |
| --- | --- |
| `-o FILE` | output file (default: the `-i` file's name, or `<board>_<thread>_<subject>.pdf` for one thread) |
| `-i FILE` | read thread links from a file, one per line |
| `--title TEXT` | title of a multi-thread PDF (default: the file name) |
| `--style NAME` | `plain`, `yotsuba` or `yotsuba-b` |
| `--refresh` | download thread pages again for new posts, and retry failed images |
| `--workdir DIR` | where downloaded threads are kept between runs (default `<output>_files/`) |
| `--max-image-px N` | downscale images whose longest side is over N px (default: never) |
| `--thumbs-only` | use thumbnails, for a small PDF |
| `--no-title-page` | leave out the title page |
| `--media-dir DIR` | where to save videos and animated GIFs (default `<output>_files/media/`) |
| `--no-save-media` | don't save them; the picture links to the original URL instead |
| `--delay S` | seconds between HTTP requests (default 1) |
| `--workers N` | parallel image downloads (default 4; they still respect `--delay`) |
| `--cache-dir DIR` / `--no-cache` | download cache (default `~/.cache/4chan2pdf`), so re-runs download nothing again |
| `--cookies FILE` | Netscape `cookies.txt`, for sites that need a session |
| `--user-agent UA` | override the User-Agent |

A summary is printed at the end. It gives the number of posts, how many images came out
full-size, thumbnail-only or failed, and the number of links between posts.

## Several threads in one PDF

Put the links in a file, one per line. Blank lines and lines starting with `#` are ignored:

```text
# Magical Girl For Hire Quest
https://suptg.thisisnotatrueending.com/qstarchive/2016/501742/
https://suptg.thisisnotatrueending.com/qstarchive/2016/513200/
```

```sh
4chan2pdf -i quest.txt                    # writes quest.pdf
4chan2pdf URL1 URL2 URL3 -o quest.pdf     # links on the command line work too
```

The PDF starts with a title page and a table of contents with page numbers, followed by one
section per thread. In the PDF outline each thread is a bookmark, with its posts nested
under it. When a post quotes a post in another thread of the same PDF, such as a quest's
link to its previous thread, the quote jumps there.

### Adding more links later

Everything downloaded is kept in a work folder next to the PDF (`quest_files/`):

```
quest_files/
  index.json     which link is stored in which file
  threads/       each parsed thread, with its posts and image details
  images/        the images embedded in the PDF
  media/         saved videos and animated GIFs, which the PDF links to
```

Add links to `quest.txt` and run the same command again. Threads already in the work folder
are taken from it, so only the new links are downloaded. A thread that fails to download is
reported and skipped; the PDF is still made from the rest, and the exit code is 1.

- `--refresh` downloads the thread pages again to pick up new posts in threads that are
  still live, and retries images that failed. Images already downloaded are reused.
- Changing `--max-image-px` or `--thumbs-only` makes the next run prepare the saved threads'
  images again.
- Keep the PDF and its `_files` folder together: the video and GIF links point into it.
- `--workdir DIR` puts the work folder somewhere else.

## Styles

`--style` changes how the pages look:

| Style | Look |
| --- | --- |
| `plain` (default) | white pages, posts separated by thin lines |
| `yotsuba` | 4chan's beige and maroon theme, with replies in boxes |
| `yotsuba-b` | 4chan's blue theme, used on work-safe boards |

## How it works

```
fetch.py   HTTP with delay, retries (429/5xx, Retry-After) and a disk cache; local files too
parse.py   4chan HTML / JSON API  ->  Thread(posts=[Post(files=[Attachment])])
links.py   sanitize post markup, rewrite quotes to #p<id>, compute backlinks
media.py   download full image (fallback: thumbnail), keep JPEG/PNG as-is, convert the rest
store.py   keep parsed threads and their images in the work folder between runs
render.py  HTML + thread.css (+ yotsuba.css)  ->  PDF with WeasyPrint; table of contents
```

Notes:

- Images are shown no larger than the page, but the full pixel data stays in the PDF, so you
  can zoom in. Identical files are stored once.
- Animated GIFs get an "animated" badge and videos a "▶ video" badge. Clicking them opens the
  saved file; clicking a still image opens its original URL. Without `ffmpeg`, a video shows
  the board's thumbnail, but the video is still saved and linked.
- Videos and GIFs are only saved when the full file is downloaded, so not with `--thumbs-only`.
- Viewers handle relative file links differently: Okular, Evince, Firefox and Acrobat open them
  (Acrobat asks first), while some browser viewers block local files.
- Images that are big for their file size show up more slowly in some PDF viewers. Use
  `--max-image-px 3000` for a lighter file.

## Development

```sh
pip install -e '.[dev]'
pytest
ruff check . && ruff format --check .
```

The tests run offline. `tests/fixtures/thread_501742.html` is a saved page in 4chan markup,
and the images are generated when the tests run. The end-to-end test builds a PDF and checks
it with `pypdf`: the embedded images are at their original pixel size, every internal link
points to an existing post destination, and the outline has one entry per post.
