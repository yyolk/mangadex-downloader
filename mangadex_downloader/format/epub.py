# MIT License

# Copyright (c) 2022-present Rahman Yusuf

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import datetime
import os
import zipfile
import shutil
import tqdm
import logging
from pathvalidate import sanitize_filename
from .base import (
    ConvertedChaptersFormat,
    ConvertedVolumesFormat,
    ConvertedSingleFormat
)
from .utils import NumberWithLeadingZeros

from ..utils import create_directory, delete_file
from ..errors import MangaDexException
from ..progress_bar import progress_bar_manager as pbm

class EpubMissingDependencies(MangaDexException):
    """Raised when `lxml` and `bs4` is not installed"""
    def __init__(self, *args, **kwargs):
        super().__init__("`lxml`, `bs4` and `Pillow` is not installed")

try:
    import lxml
    from bs4 import BeautifulSoup, Doctype, Comment
    from PIL import Image
except ImportError:
    epub_ready = False
else:
    epub_ready = True

log = logging.getLogger(__name__)

# Inspired from https://github.com/manga-download/hakuneko/blob/master/src/web/mjs/engine/EbookGenerator.mjs
# TODO: Add doc for this class
class EpubPlugin:
    def __init__(self, manga, lang):
        self.manga = manga
        self.id = manga.id
        self.title = manga.title
        self.lang = lang

        self._pos = 0
        self._pages = {}

        # Container.xml
        self._container = None
        self._make_container()

        # for .opf document
        self._opf_root = None
        self._manifest = None
        self._spine = None
        self._make_opf()

        # toc.ncx
        self._toc_root = None
        self._navigation = None
        self._make_toc()

        # nav.xhtml
        self._nav_root = None
        self._make_nav(manga.title)


    def _make_toc(self):
        root = self._get_root()
        self._toc_root = root

        # <ncx>
        ncx = root.new_tag(
            'ncx',
            attrs={
                'xmlns': 'http://www.daisy.org/z3986/2005/ncx/',
                'version': '2005-1',
                "xmlns:ncx": "http://www.daisy.org/z3986/2005/ncx/",
            }
        )
        root.append(ncx)

        # <head>
        head = root.new_tag('head')
        meta_tag = root.new_tag(
            'meta',
            attrs={
                'name': 'dtb:uid',
                'content': f'urn:uuid:{self.id}'
            }
        )
        head.append(meta_tag)
        ncx.append(head)

        # <docTitle>
        doc_title = root.new_tag('docTitle')
        text_tag = root.new_tag('text')
        text_tag.string = self.title
        doc_title.append(text_tag)
        ncx.append(doc_title)

        # <navMap>
        nav = root.new_tag('navMap')
        ncx.append(nav)
        self._navigation = nav

    def _make_opf(self):
        root = self._get_root()
        self._opf_root = root

        package = root.new_tag(
            'package',
            attrs={
                'xmlns': 'http://www.idpf.org/2007/opf',
                'unique-identifier': "BookID",
                'version': '3.0'
            }
        )
        root.append(package)

        # <metadata>
        metadata = root.new_tag(
            'metadata',
            attrs={
                'xmlns:dc': 'http://purl.org/dc/elements/1.1/',
                'xmlns:opf': 'http://www.idpf.org/2007/opf'
            }
        )
        dc_title = root.new_tag('dc:title')
        dc_title.string = self.title
        dc_language = root.new_tag('dc:language')
        dc_language.string = self.lang
        dc_id = root.new_tag(
            'dc:identifier',
            attrs={
                'id': "BookID",
            }
        )
        dc_id.string = f'urn:uuid:{self.id}'

        # Tags
        for tag in self.manga.tags:
            dc_tag = root.new_tag('dc:subject')
            dc_tag.string = tag.name
            metadata.append(dc_tag)

        # Authors
        authors = ""
        for index, author in enumerate(self.manga.authors):
            if index < (len(self.manga.authors) - 1):
                authors += author + ","
            else:
                # If this is last index, append author without comma
                authors += author
        dc_authors = root.new_tag('dc:creator')
        dc_authors.string = authors
        
        # Modified
        dcterms_modified = root.new_tag(
            'meta',
            attrs={
                'property': "dcterms:modified"
            }
        )
        dcterms_modified.string = f'{datetime.datetime.utcnow():%Y-%m-%dT%H:%M:%SZ}'
        # Right to Left
        primary_writing_mode = root.new_tag(
            'meta',
            attrs={
                'name': 'primary-writing-mode',
                'content': 'horizontal-rl',
            }
        )
        book_type = root.new_tag(
            'meta',
            attrs={
                'name': 'book-type',
                'content': 'comic',
            }
        )

        metadata.append(dc_authors)

        metadata.append(dc_title)
        metadata.append(dc_language)
        metadata.append(dc_id)
        metadata.append(primary_writing_mode)
        metadata.append(book_type)
        package.append(metadata)

        # <manifest>
        manifest = root.new_tag('manifest')
        ncx_tag = root.new_tag(
            'item',
            attrs={
                'id': 'ncx',
                'href': 'toc.ncx',
                'media-type': 'application/x-dtbncx+xml'
            }
        )
        manifest.append(ncx_tag)
        package.append(manifest)
        self._manifest = manifest

        # <spine>
        spine = root.new_tag('spine', attrs={'toc': 'ncx'})
        package.append(spine)
        self._spine = spine

    def _get_root(self):
        return BeautifulSoup("", "xml")

    def _create_nav(self, _id, text, src=None):
        navpoint_kwargs = {
            'id': _id,
        }

        toc = self._toc_root.new_tag(
            'navPoint',
            attrs=navpoint_kwargs
        )
        toc_label = self._toc_root.new_tag('navLabel')
        toc_text = self._toc_root.new_tag('text')
        toc_text.string = text
        toc_label.append(toc_text)
        toc.append(toc_label)

        if src:
            toc_content = self._toc_root.new_tag(
                'content',
                attrs={
                    'src': src
                }
            )
            toc.append(toc_content)
        
        return toc

    def _make_nav(self, title):
        root = self._get_root()
        self._nav_root = root
        # Make doctype
        doctype = Doctype.for_name_and_ids("html", None, None)
        root.append(doctype)

        html_root = root.new_tag(
            'html',
            attrs={
                'xmlns': 'http://www.w3.org/1999/xhtml',
                'xmlns:epub': 'http://www.idpf.org/2007/ops',
            }
        )

        # Head document
        head_root = root.new_tag('head')
        title_tag = root.new_tag('title')
        title_tag.string = title
        head_root.append(title_tag)

        meta_charset = root.new_tag(
            'meta',
            attrs={
                'charset': "utf-8"
            }
        )
        head_root.append(meta_charset)

        # Body document
        body_root = root.new_tag('body')
        nav_tag = root.new_tag(
            'nav',
            attrs={
                'xmlns:epub': 'http://www.idpf.org/2007/ops',
                'epub:type': 'toc',
                'id': 'toc',
            }
        )
        body_root.append(nav_tag)

        # ol>li>a[href=0000.xhtml]{Title}
        nav_ol = root.new_tag('ol')
        nav_ol_li = root.new_tag('li')
        nav_ol_li_a = root.new_tag(
            'a',
            attrs={
                # TODO: this is a guess
                'href': 'xhtml/0_1.xhtml'
            }
        )
        nav_ol_li_a.string = title

        nav_ol_li.append(nav_ol_li_a)
        nav_ol.append(nav_ol_li)
        body_root.append(nav_ol)

        nav_page_list = root.new_tag(
            'nav',
            attrs={
                'epub:type': 'page-list'
            }
        )
        nav_pagelist_ol = root.new_tag('ol')
        nav_pagelist_ol_li = root.new_tag('li')
        nav_pagelist_ol_li_a = root.new_tag(
            'a',
            attrs={
                # TODO: this is a guess
                'href': 'xhtml/0_1.xhtml'
            }
        )
        nav_pagelist_ol_li_a.string = title
        body_root.append(nav_page_list)

        nav_pagelist_ol_li.append(nav_pagelist_ol_li_a)
        nav_pagelist_ol.append(nav_pagelist_ol_li)
        body_root.append(nav_pagelist_ol)

        # HTML root
        html_root.append(head_root)
        html_root.append(body_root)
        root.append(html_root)

        # self._create_manifest_item(self._pos, pos, im)
        # self._create_spine_item(self._pos, pos)
        # self._create_toc_item(nav, self._pos, pos)

        # xhtml.append(root)

    def _create_toc_item(self, nav, path, pos):
        xhtml_path = f'xhtml/{path}_{pos}.xhtml'
        nav_point = self._create_nav(
            f'TOC_{path}_{pos}',
            f'Page {pos}',
            xhtml_path
        )
        nav.append(nav_point)

    def _create_manifest_item(self, path, pos, image):
        im_name = os.path.basename(image)
        im = Image.open(image)

        xhtml_item = self._opf_root.new_tag(
            'item',
            attrs={
                'id': f'XHTML_{path}_{pos}',
                'href': f'xhtml/{path}_{pos}.xhtml',
                'media-type': 'application/xhtml+xml'
            }
        )
        img_item = self._opf_root.new_tag(
            'item',
            attrs={
                'id': f'IMAGES_{path}_{pos}',
                'href': f'images/{path}_{im_name}',
                'media-type': Image.MIME.get(im.format)
            }
        )
        im.close()

        self._manifest.append(xhtml_item)
        self._manifest.append(img_item)

    def _create_spine_item(self, path, pos):
        item = self._opf_root.new_tag(
            'itemref',
            attrs={
                'idref': f'XHTML_{path}_{pos}'
            }
        )
        self._spine.append(item)

    def create_page(self, title, images):
        # Create page toc
        nav = self._create_nav(
            f"TOC_{self._pos}_INIT",
            title,
            f"xhtml/{self._pos}_1.xhtml"
        )
        xhtml = []

        for pos, im in enumerate(images, start=1):
            image = os.path.basename(im)
            root = self._get_root()

            # Make doctype
            doctype = Doctype.for_name_and_ids("html")
            root.append(doctype)

            html_root = root.new_tag(
                'html',
                attrs={
                    'xmlns': 'http://www.w3.org/1999/xhtml'
                }
            )

            # Head document
            head_root = root.new_tag('head')
            title_tag = root.new_tag('title')
            title_tag.string = title
            head_root.append(title_tag)

            # Body document
            body_root = root.new_tag('body')
            div_tag = root.new_tag('div')
            img_tag = root.new_tag(
                'img',
                attrs={
                    'alt': image,
                    'src': f'../images/{self._pos}_{image}',
                }
            )
            div_tag.append(img_tag)
            body_root.append(div_tag)

            # HTML root
            html_root.append(head_root)
            html_root.append(body_root)
            root.append(html_root)

            self._create_manifest_item(self._pos, pos, im)
            self._create_spine_item(self._pos, pos)
            self._create_toc_item(nav, self._pos, pos)

            xhtml.append(root)

        self._navigation.append(nav)

        self._pages[self._pos] = [
            xhtml,
            images
        ]

        self._pos += 1

    def _make_container(self):
        root = self._get_root()
        container_tag = root.new_tag(
            "container",
            attrs={
                "version": "1.0",
                "xmlns": "urn:oasis:names:tc:opendocument:xmlns:container"
            }
        )
        rootfiles_tag = root.new_tag("rootfiles")
        rootfile_tag = root.new_tag(
            "rootfile",
            attrs={
                "full-path": "OEBPS/content.opf",
                "media-type": "application/oebps-package+xml"
            }
        )

        rootfiles_tag.append(rootfile_tag)
        container_tag.append(rootfiles_tag)
        root.append(container_tag)

        self._container = root
    
    def write(self, path):
        from ..config import env

        # Calculate all images and set it to progress bar convert total
        total_images = 0
        for _, (_, images) in self._pages.items():
            total_images += len(images)

        pbm.set_convert_total(total_images)
        progress_bar = pbm.get_convert_pb(recreate=not pbm.stacked)

        with zipfile.ZipFile(
            path, 
            "a" if os.path.exists(path) else "w",
            compression=env.zip_compression_type,
            compresslevel=env.zip_compression_level
        ) as zip_obj:
            # Write MIMETYPE
            zip_obj.writestr('mimetype', 'application/epub+zip')

            # Write container
            zip_obj.writestr('META-INF/container.xml', self._container.prettify())

            # Write table of contents
            zip_obj.writestr('OEBPS/toc.ncx', str(self._toc_root))

            # Write .opf document
            zip_obj.writestr('OEBPS/content.opf', self._opf_root.prettify())

            # Write nav
            zip_obj.writestr('OEBPS/nav.xhtml', self._nav_root.prettify())

            # Write XHTML and images
            for page, (xhtml, images) in self._pages.items():

                for pos, content in enumerate(xhtml, start=1):
                    zip_obj.writestr(f'OEBPS/xhtml/{page}_{pos}.xhtml', content.prettify())

                for pos, image in enumerate(images, start=1):
                    zip_obj.write(
                        image, 
                        f'OEBPS/images/{page}_{os.path.basename(image)}'
                    )

                    progress_bar.update(1)

class EPUBFile:
    file_ext = ".epub"

    def check_dependecies(self):
        if not epub_ready:
            raise EpubMissingDependencies()

    def convert(self, manga, lang, chapters, path):
        epub = EpubPlugin(manga, lang)

        for chapter, images in chapters:
            epub.create_page(chapter.get_name(), images)
        
        epub.write(path)

class Epub(ConvertedChaptersFormat, EPUBFile):
    def on_finish(self, file_path, chapter, images):
        chap_name = chapter.get_simplified_name()

        pbm.logger.info(f"{chap_name} has finished download, converting to epub...")
        # KeyboardInterrupt safe
        job = lambda: self.convert(
            self.manga,
            chapter.language.value,
            [(chapter, images)],
            file_path
        )
        self.worker.submit(job)

class EpubVolume(ConvertedVolumesFormat, EPUBFile):
    def on_prepare(self, file_path, volume, count):
        self.epub_chapters = []

    def on_convert(self, file_path, volume, images):
        volume_name = self.get_volume_name(volume)

        pbm.logger.info(f"{volume_name} has finished download, converting to epub...")

        job = lambda: self.convert(
            self.manga,
            self.manga.chapters.language.value,
            self.epub_chapters,
            file_path
        )
        self.worker.submit(job)

    def on_received_images(self, file_path, chapter, images):
        self.epub_chapters.append((chapter, images))

class EpubSingle(ConvertedSingleFormat, EPUBFile):
    def on_prepare(self, file_path, base_path):
        self.epub_chapters = []

    def on_finish(self, file_path, images):
        pbm.logger.info(f"Manga '{self.manga.title}' has finished download, converting to epub...")

        job = lambda: self.convert(
            self.manga,
            self.manga.chapters.language.value,
            self.epub_chapters,
            file_path
        )
        self.worker.submit(job)

    def on_received_images(self, file_path, chapter, images):
        self.epub_chapters.append((chapter, images))