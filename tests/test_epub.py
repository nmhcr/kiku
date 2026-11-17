import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from audiobook_creator.book import chapters_from_source, load_source
from audiobook_creator.epub import read_epub

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0">
  <metadata>
    <dc:title>A Test Book</dc:title>
    <dc:creator>Jane Doe</dc:creator>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="nav" linear="no"/>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>
"""

NAV = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Table of Contents</h1><p>Chapter One, Chapter Two</p></body></html>"""


def page(title: str, body: str) -> str:
    return (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><title>skip me</title><script>bad()</script><style>x{}</style></head>"
        f"<body><h1>{title}</h1><p>{body}</p><p>More text here.</p></body></html>"
    )


def make_epub(path: Path, first: str = "Chapter One", second: str = "Chapter Two") -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/nav.xhtml", NAV)
        archive.writestr("OEBPS/chapter1.xhtml", page(first, "Hello &amp; welcome."))
        archive.writestr("OEBPS/chapter2.xhtml", page(second, "The second chapter."))
    return path


class EpubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.epub = make_epub(self.root / "book.epub")

    def tearDown(self):
        self.tmp.cleanup()

    def test_metadata_and_text(self):
        source = read_epub(self.epub)
        self.assertEqual(source.title, "A Test Book")
        self.assertEqual(source.author, "Jane Doe")
        self.assertIn("Hello & welcome.", source.text)
        self.assertIn("The second chapter.", source.text)
        self.assertNotIn("bad()", source.text)
        self.assertNotIn("Table of Contents", source.text)

    def test_documents_follow_spine_order(self):
        source = read_epub(self.epub)
        self.assertEqual(len(source.documents), 2)
        self.assertEqual(source.documents[0][0], "Chapter One")
        self.assertEqual(source.documents[1][0], "Chapter Two")

    def test_headings_split_into_chapters(self):
        source = read_epub(self.epub)
        chapters = chapters_from_source(source)
        # The capture group of the heading regex holds the title after "Chapter".
        self.assertEqual([chapter.title for chapter in chapters], ["One", "Two"])

    def test_document_boundaries_when_no_headings(self):
        epub = make_epub(
            self.root / "noheadings.epub",
            first="The Beginning",
            second="The End",
        )
        chapters = chapters_from_source(read_epub(epub))
        self.assertEqual([chapter.title for chapter in chapters], ["The Beginning", "The End"])

    def test_load_source_dispatch(self):
        txt = self.root / "plain.txt"
        txt.write_text("Chapter One\nSome text.", encoding="utf-8")
        self.assertEqual(load_source(txt).documents, [])
        self.assertEqual(len(load_source(self.epub).documents), 2)

    def test_invalid_epub(self):
        broken = self.root / "broken.epub"
        broken.write_bytes(b"not a zip")
        with self.assertRaises(ValueError):
            read_epub(broken)

    def test_init_uses_epub_metadata(self):
        project = self.root / "project"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "audiobook_creator",
                "init",
                "--source",
                str(self.epub),
                "--dir",
                str(project),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        config = (project / "book.toml").read_text(encoding="utf-8")
        self.assertIn('title = "A Test Book"', config)
        self.assertIn('author = "Jane Doe"', config)
        self.assertIn('path = "book.epub"', config)


if __name__ == "__main__":
    unittest.main()
