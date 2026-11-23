"""Notes de bas de page : détection, composition en bas de page, section retirée."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from converter.assembler import Options, build_document
from converter.renderer import render_pdf
from test_layout_and_structure import _document

_PARAGRAPH = (
    "Le vent tournait autour de la grange tandis que les animaux achevaient "
    "leur journée de travail, et chacun regagnait sa place en silence. "
)

_CHAPTER = (
    "<h1>Chapitre</h1>"
    + "".join(f"<p>{_PARAGRAPH * 4}</p>" for _ in range(2))
    + '<p>Une phrase avec un renvoi<a epub:type="noteref" href="notes.xhtml#n1" id="r1">1</a>'
    f" avant la fin. {_PARAGRAPH * 2}</p>"
)

# La note contient un saut de ligne et un paragraphe : le texte doit rester en ligne.
_NOTES = (
    '<section epub:type="endnotes"><h1>Notes</h1><ol>'
    '<li id="n1">Une note de bas de page bien réelle.\n<p>Un second paragraphe de note.</p>'
    "</li></ol></section>"
)


def _options() -> Options:
    return Options(include_cover_page=False, include_toc=False)


def _build(directory: Path, chapters: dict[str, str]) -> tuple[str, Path]:
    document, media = _document(directory, chapters)
    return build_document(document, media, _options()), media


def _render(directory: Path, chapters: dict[str, str]) -> bytes:
    html, media = _build(directory, chapters)
    return render_pdf(html, base_url=media)


def _texts(pdf: bytes) -> list[str]:
    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages]


def _flat(text: str) -> str:
    """Texte compacté : l'extraction coupe les lignes au gré de la justification."""
    return " ".join(text.split())


class FootnoteTest(unittest.TestCase):
    """Un renvoi de note doit poser la note en bas de page, pas à la fin du livre."""

    def test_note_is_composed_at_bottom_of_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _render(Path(tmp), {"ch1.xhtml": _CHAPTER, "notes.xhtml": _NOTES})

        texts = _texts(pdf)
        page = next(index for index, text in enumerate(texts) if "Une phrase avec un renvoi" in text)
        text = _flat(texts[page])
        self.assertIn("Une note de bas de page bien réelle.", text)
        self.assertIn("Un second paragraphe de note.", text)
        self.assertLess(
            text.index("Une phrase avec un renvoi"),
            text.index("Un second paragraphe de note."),
        )
        # Le paragraphe appelant n'est pas coupé par la note insérée.
        self.assertIn("avant la fin.", text)
        # La section de notes vidée ne produit plus de page ni d'en-tête courant.
        self.assertNotIn("Notes", _flat(" ".join(texts)))

    def test_call_and_backlink_are_clickable(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _render(Path(tmp), {"ch1.xhtml": _CHAPTER, "notes.xhtml": _NOTES})

        reader = PdfReader(io.BytesIO(pdf))
        names = set(reader.named_destinations or {})
        self.assertIn("reliure-fn-1", names)
        # L'identifiant d'origine du renvoi est conservé pour les liens d'une note à l'autre.
        call_id = next(name for name in names if name.endswith("-r1"))

        targets = []
        for page in reader.pages:
            for annot in page.get("/Annots") or []:
                data = annot.get_object()
                dest = data.get("/A", {}).get("/D") or data.get("/Dest")
                if dest is not None:
                    targets.append(str(dest))
        self.assertIn("reliure-fn-1", targets)
        self.assertIn(call_id, targets)

    def test_note_to_note_link_reaches_the_footnote(self):
        chapters = {
            "ch1.xhtml": (
                "<h1>Chapitre</h1><p>Un renvoi"
                '<a epub:type="noteref" href="notes.xhtml#n1" id="r1">1</a> et un second'
                '<a epub:type="noteref" href="notes.xhtml#n2" id="r2">2</a>.</p>'
            ),
            "notes.xhtml": (
                '<section epub:type="endnotes"><h1>Notes</h1><ol>'
                '<li id="n1">Première note, voir la <a href="#n2">deuxième note</a>.</li>'
                '<li id="n2">Deuxième note.</li>'
                "</ol></section>"
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            html, media = _build(Path(tmp), chapters)
            pdf = render_pdf(html, base_url=media)

        # Le renvoi de la note 1 vers la note 2 vise la note de bas de page, plus la section.
        self.assertIn('href="#reliure-fn-2"', html)
        reader = PdfReader(io.BytesIO(pdf))
        self.assertIn("reliure-fn-2", set(reader.named_destinations or {}))
        text = _flat(" ".join(_texts(pdf)))
        self.assertIn("Première note, voir la deuxième note.", text)
        self.assertIn("Deuxième note.", text)

    def test_typed_aside_becomes_a_footnote(self):
        chapter = (
            "<h1>Chapitre</h1><p>Un paragraphe.</p>"
            '<aside epub:type="footnote">Une note posée dans le texte.</aside>'
            "<p>La suite du chapitre.</p>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _render(Path(tmp), {"ch1.xhtml": chapter})

        text = _flat(_texts(pdf)[0])
        self.assertIn("Une note posée dans le texte.", text)
        self.assertLess(text.index("Un paragraphe."), text.index("Une note posée dans le texte."))
        self.assertIn("La suite du chapitre.", text)
        names = set(PdfReader(io.BytesIO(pdf)).named_destinations or {})
        self.assertTrue({"reliure-fn-1", "reliure-fnref-1"} <= names)

    def test_plain_cross_reference_is_left_alone(self):
        chapters = {
            "ch1.xhtml": '<h1>Chapitre</h1><p>Voir <a href="ch2.xhtml#sec">la suite</a>.</p>',
            "ch2.xhtml": '<h1>Suite</h1><p id="sec">Le passage en question.</p>',
        }
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _render(Path(tmp), chapters)

        reader = PdfReader(io.BytesIO(pdf))
        self.assertFalse(
            [name for name in (reader.named_destinations or {}) if name.startswith("reliure-fn")]
        )
        self.assertIn("Voir la suite.", _flat(" ".join(_texts(pdf))))


if __name__ == "__main__":
    unittest.main()
