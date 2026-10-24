# Copyright 2026 alininium
# Distributed under the terms of the MIT License

EAPI=8

DISTUTILS_USE_PEP517=setuptools
PYTHON_COMPAT=( python3_{11..14} )

inherit distutils-r1 git-r3 optfeature

DESCRIPTION="Save imageboard threads as PDFs with full-size images and links between posts"
HOMEPAGE="https://github.com/alininium/4chan2pdf"
EGIT_REPO_URI="https://github.com/alininium/4chan2pdf.git"

LICENSE="MIT"
SLOT="0"

RDEPEND="
	dev-python/beautifulsoup4[${PYTHON_USEDEP}]
	dev-python/httpx[${PYTHON_USEDEP}]
	dev-python/lxml[${PYTHON_USEDEP}]
	dev-python/pillow[${PYTHON_USEDEP}]
	dev-python/weasyprint[${PYTHON_USEDEP}]
	media-fonts/dejavu
"
BDEPEND="
	test? (
		dev-python/pypdf[${PYTHON_USEDEP}]
	)
"

distutils_enable_tests pytest

pkg_postinst() {
	optfeature "first frames of webm/mp4 videos" media-video/ffmpeg
	optfeature "WebP images" "dev-python/pillow[webp]"
}
