"""Non-régression du rapport qualité : PDF propre et PDF avec défauts forcés."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from converter import quality


def _build_pdf(*, blank_page: bool, embedded_font: bool, stray: str = "") -> bytes:
    """PDF d'une page de texte, avec page blanche et police incorporée optionnelles.

    `stray` ajoute un second run de texte à des coordonnées très au-delà de la page,
    pour reproduire les blancs positionnés hors page de certains EPUB.
    """
    writer = PdfWriter()
    writer.add_metadata({"/Title": "Test qualité", "/Author": "Reliure"})

    descriptor = DictionaryObject()
    if embedded_font:
        font_file = DecodedStreamObject()
        font_file.set_data(b"")
        descriptor[NameObject("/FontFile2")] = writer._add_object(font_file)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/TrueType"),
            NameObject("/BaseFont"): NameObject("/TestFont"),
            NameObject("/FontDescriptor"): writer._add_object(descriptor),
        }
    )
    font_ref = writer._add_object(font)

    page = writer.add_blank_page(width=595, height=842)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    stream = DecodedStreamObject()
    content = b"BT /F1 24 Tf 72 720 Td (Controle qualite) Tj ET"
    if stray:
        content += b"\nBT /F1 24 Tf 20000 20000 Td (" + stray.encode("latin-1") + b") Tj ET"
    stream.set_data(content)
    page[NameObject("/Contents")] = writer._add_object(stream)

    if blank_page:
        writer.add_blank_page(width=595, height=842)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _warnings(report: dict) -> list[str]:
    return [check["label"] for check in report["checks"] if check["state"] == "warn"]


class QualityReportTest(unittest.TestCase):
    def test_warn_for_blank_page_and_unembedded_font(self):
        report = quality.inspect(
            _build_pdf(blank_page=True, embedded_font=False), expect_bookmarks=False
        )

        self.assertEqual(report["state"], "warn")
        self.assertEqual(report["pages"], 2)
        self.assertEqual(
            _warnings(report),
            ["1 page vide : p. 2", "1 police non incorporée : TestFont"],
        )

    def test_whitespace_run_outside_page_is_ignored(self):
        report = quality.inspect(
            _build_pdf(blank_page=False, embedded_font=True, stray="   "),
            expect_bookmarks=False,
        )

        self.assertEqual(report["state"], "ok")
        self.assertEqual(_warnings(report), [])

    def test_real_text_outside_page_is_still_reported(self):
        report = quality.inspect(
            _build_pdf(blank_page=False, embedded_font=True, stray="Perdu"),
            expect_bookmarks=False,
        )

        self.assertEqual(report["state"], "warn")
        self.assertEqual(_warnings(report), ["1 fragment de texte hors page"])

    def test_clean_pdf_stays_ok(self):
        report = quality.inspect(
            _build_pdf(blank_page=False, embedded_font=True), expect_bookmarks=False
        )

        self.assertEqual(report["state"], "ok")
        self.assertEqual(report["pages"], 1)
        self.assertEqual(_warnings(report), [])


if __name__ == "__main__":
    unittest.main()
