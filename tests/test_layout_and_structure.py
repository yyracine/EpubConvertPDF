"""Non-régression : réglages de mise en page appliqués au texte, classification documentaire."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from converter import epub_reader
from converter.assembler import Options, build_document
from converter.renderer import render_pdf

_CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">reliure-test</dc:identifier>
    <dc:title>Livre de test</dc:title>
    <dc:language>fr</dc:language>
    <dc:creator>Reliure</dc:creator>
  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>
"""


def _page(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        f"<head><title>Test</title></head><body>{body}</body></html>"
    )


def _write_epub(directory: Path, chapters: dict[str, str], css: str = "") -> Path:
    """EPUB 3 minimal, écrit à la main pour garder les attributs `epub:type` intacts."""
    manifest = ['    <item id="css" href="style.css" media-type="text/css"/>']
    spine = []
    for index, name in enumerate(chapters, start=1):
        manifest.append(
            f'    <item id="c{index}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        spine.append(f'    <itemref idref="c{index}"/>')

    target = directory / "test.epub"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", _CONTAINER)
        archive.writestr(
            "OEBPS/content.opf",
            _OPF.format(manifest="\n".join(manifest), spine="\n".join(spine)),
        )
        archive.writestr("OEBPS/style.css", css)
        for name, body in chapters.items():
            archive.writestr(f"OEBPS/{name}", _page(body))
    return target


def _document(directory: Path, chapters: dict[str, str], css: str = ""):
    path = _write_epub(directory, chapters, css)
    document = epub_reader.read_book(path)
    media = directory / "media"
    epub_reader.extract_resources(document, media)
    return document, media


def _font_sizes(pdf_bytes: bytes) -> Counter:
    """Nombre de caractères rendus pour chaque taille de police du PDF."""
    sizes: Counter = Counter()

    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:

        def visit(text, _cm, _tm, _font_dict, font_size, sizes=sizes):  # noqa: ANN001
            if text and text.strip():
                sizes[round(float(font_size or 0), 2)] += len(text.strip())

        page.extract_text(visitor_text=visit)
    return sizes


def _text_metrics(pdf_bytes: bytes) -> tuple[float, float]:
    """Taille de corps dominante et interligne médian, mesurés dans le PDF."""
    sizes: Counter = Counter()
    gaps: list[float] = []

    for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
        lines: list[float] = []

        def visit(text, _cm, tm, _font_dict, font_size, lines=lines):  # noqa: ANN001
            if not text or not text.strip():
                return
            sizes[round(float(font_size or 0), 2)] += len(text.strip())
            lines.append(round(float(tm[5]), 2))

        page.extract_text(visitor_text=visit)
        ordered = sorted(set(lines))
        gaps.extend(b - a for a, b in zip(ordered, ordered[1:]) if b - a > 0.5)

    body_size = sizes.most_common(1)[0][0]
    body_gaps = sorted(gap for gap in gaps if gap < body_size * 4)
    return body_size, body_gaps[len(body_gaps) // 2]


_AGGRESSIVE_CSS = """
body { font-size: 15pt; line-height: 1.1; }
p { font-size: 12pt !important; line-height: 1.1; }
p.lede { font-size: 8pt !important; letter-spacing: 0.02em; }
"""

# Le corps figé par un raccourci `font`, comme celui posé par certaines feuilles d'EPUB.
_SHORTHAND_CSS = """
p { font: 12pt/1.1 Georgia, "Times New Roman", serif; }
p.lede { font: italic 8pt/1.05 Georgia, serif; }
"""

_PARAGRAPH = (
    "Le vent tournait autour de la grange tandis que les animaux achevaient "
    "leur journée de travail, et chacun regagnait sa place en silence. "
)


class LayoutOverrideTest(unittest.TestCase):
    """Le corps et l'interligne choisis doivent gagner sur le CSS de l'EPUB."""

    def _render(self, directory: Path, options: Options, css: str = _AGGRESSIVE_CSS) -> bytes:
        paragraphs = "".join(
            f"<p>{_PARAGRAPH * 2}</p>" for _ in range(4)
        )
        document, media = _document(
            directory,
            {"ch1.xhtml": f"<h1>Chapitre</h1>{paragraphs}"
             f'<p class="lede">{_PARAGRAPH * 2}</p>'},
            css,
        )
        html = build_document(document, media, options)
        return render_pdf(html, base_url=media)

    def test_absolute_sizes_scale_with_the_chosen_body(self):
        notes: list[float] = []
        for body_size in (10.0, 12.0):
            with tempfile.TemporaryDirectory() as tmp:
                rendered = self._render(
                    Path(tmp),
                    Options(
                        font_size=body_size,
                        line_height=1.55,
                        include_cover_page=False,
                        include_toc=False,
                    ),
                )
            body, note = [size for size, _chars in _font_sizes(rendered).most_common(2)]
            # La légende (8 pt) reste plus petite que le texte (12 pt) dans le même rapport.
            self.assertAlmostEqual(note / body, 8 / 12, delta=0.02)
            notes.append(note)

        # Et elle grandit avec le corps choisi au lieu de rester figée.
        self.assertAlmostEqual(notes[1] / notes[0], 1.2, delta=0.05)

    def test_font_shorthand_size_follows_the_chosen_body(self):
        bodies: list[float] = []
        for body_size in (10.0, 12.0):
            with tempfile.TemporaryDirectory() as tmp:
                rendered = self._render(
                    Path(tmp),
                    Options(
                        font_size=body_size,
                        line_height=1.55,
                        include_cover_page=False,
                        include_toc=False,
                    ),
                    _SHORTHAND_CSS,
                )
            body, note = [size for size, _chars in _font_sizes(rendered).most_common(2)]
            # La légende du raccourci (8 pt) reste à deux tiers du texte (12 pt).
            self.assertAlmostEqual(note / body, 8 / 12, delta=0.02)
            bodies.append(body)

        # Le texte du raccourci suit le corps choisi au lieu de rester à 12 pt.
        self.assertAlmostEqual(bodies[1] / bodies[0], 1.2, delta=0.05)

    def test_shorthand_line_height_does_not_override_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, tight = _text_metrics(
                self._render(
                    directory, Options(font_size=11.0, line_height=1.4), _SHORTHAND_CSS
                )
            )
            _, loose = _text_metrics(
                self._render(
                    directory, Options(font_size=11.0, line_height=1.7), _SHORTHAND_CSS
                )
            )

        # `/1.1` du raccourci est retiré : l'interligne choisi s'applique et varie.
        self.assertAlmostEqual(loose / tight, 1.7 / 1.4, delta=0.06)

    def test_font_size_setting_reaches_body_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            small, _ = _text_metrics(
                self._render(directory, Options(font_size=10.0, line_height=1.55))
            )
            large, _ = _text_metrics(
                self._render(directory, Options(font_size=12.0, line_height=1.55))
            )

        self.assertAlmostEqual(large / small, 1.2, delta=0.05)

    def test_line_height_setting_reaches_body_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, tight = _text_metrics(
                self._render(directory, Options(font_size=11.0, line_height=1.4))
            )
            _, loose = _text_metrics(
                self._render(directory, Options(font_size=11.0, line_height=1.7))
            )

        self.assertAlmostEqual(loose / tight, 1.7 / 1.4, delta=0.06)


class DocumentLevelTypeTest(unittest.TestCase):
    """Seuls les types portés par un élément de niveau document classent un fichier."""

    def test_inner_link_type_does_not_classify_chapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            document, _ = _document(
                Path(tmp),
                {
                    "ch1.xhtml": (
                        "<h1>Chapitre un</h1>"
                        '<p>Un renvoi vers <a href="ch2.xhtml#n1" epub:type="cover">'
                        "la suite</a> ne doit rien changer.</p>"
                        '<p><span epub:type="toc">Un fragment typé</span> non plus.</p>'
                    ),
                    "ch2.xhtml": "<h1>Chapitre deux</h1><p id=\"n1\">La suite du texte.</p>",
                },
            )

        self.assertEqual([chapter.kind for chapter in document.chapters], ["body", "body"])

    def test_document_level_type_still_classifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            document, _ = _document(
                Path(tmp),
                {
                    "body.xhtml": "<h1>Chapitre un</h1><p>Le texte commence ici.</p>",
                    "toc.xhtml": (
                        '<nav epub:type="toc"><ol><li><a href="body.xhtml">Chapitre un</a>'
                        "</li></ol></nav>"
                    ),
                },
            )

        kinds = [chapter.kind for chapter in document.chapters]
        self.assertEqual(kinds, ["body", "toc"])


if __name__ == "__main__":
    unittest.main()
