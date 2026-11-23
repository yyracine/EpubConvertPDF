"""Contrôle qualité du PDF produit : pages, texte, signets, liens, polices."""

from __future__ import annotations

import io

import lxml.html
from pypdf import PdfReader
from pypdf.generic import IndirectObject

from .epub_reader import EpubDocument

_MIN_TEXT_RATIO = 0.95
_PAGE_LIST_LIMIT = 8


def inspect(
    pdf_bytes: bytes,
    *,
    document: EpubDocument | None = None,
    expect_bookmarks: bool = True,
) -> dict:
    """Audite le PDF rendu : nombre de pages, état et liste de contrôles.

    Un échec du contrôle ne doit jamais faire échouer la conversion : le rapport
    est alors vide et l'interface masque le bloc.
    """
    try:
        return _inspect(pdf_bytes, document=document, expect_bookmarks=expect_bookmarks)
    except Exception:
        return {"state": "info", "pages": 0, "checks": []}


def _inspect(pdf_bytes: bytes, *, document: EpubDocument | None, expect_bookmarks: bool) -> dict:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = list(reader.pages)
    page_ids = {
        page.indirect_reference.idnum
        for page in pages
        if getattr(page, "indirect_reference", None) is not None
    }

    words = 0
    outside = 0
    images = 0
    empty_pages: list[int] = []

    for number, page in enumerate(pages, start=1):
        text, page_outside = _scan_page(page)
        page_images = _page_images(page)
        words += len(text.split())
        outside += page_outside
        images += page_images
        if not text.strip() and page_images == 0:
            empty_pages.append(number)

    font_count, missing_fonts = _fonts(pages)
    link_count, broken_links = _links(pages, reader, page_ids)
    bookmarks = _count_outline(reader.outline)
    metadata = reader.metadata or {}
    title = str(metadata.get("/Title") or "").strip()
    author = str(metadata.get("/Author") or "").strip()
    source_words, source_images = _source_totals(document)

    checks: list[dict[str, str]] = []
    state = "ok"

    def note(kind: str, label: str) -> None:
        nonlocal state
        checks.append({"state": kind, "label": label})
        if kind == "warn":
            state = "warn"

    note("info", f"{_quantity(len(pages), 'page')} · {_number(words)} mots")

    if empty_pages:
        note(
            "warn",
            f"{_quantity(len(empty_pages), 'page vide', 'pages vides')} : {_page_list(empty_pages)}",
        )
    else:
        note("ok", "Aucune page blanche")

    if source_words:
        percent = round(words * 100 / source_words)
        if percent >= 100:
            # En-têtes courants, folios et sommaire ajoutent des mots au PDF.
            note("ok", f"Texte complet : {percent} % des mots de l'EPUB (en-têtes et folios compris)")
        elif percent >= _MIN_TEXT_RATIO * 100:
            note("ok", f"Texte retrouvé : {percent} % des mots de l'EPUB")
        else:
            note("warn", f"Texte incomplet : {percent} % des mots de l'EPUB seulement")

    if expect_bookmarks:
        if bookmarks:
            note("ok", _quantity(bookmarks, "signet navigable", "signets navigables"))
        else:
            note("warn", "Aucun signet PDF alors que le sommaire est demandé")

    if link_count:
        if broken_links:
            note(
                "warn",
                f"{_quantity(broken_links, 'lien interne', 'liens internes')} sans destination"
                f" sur {_number(link_count)}",
            )
        else:
            note("ok", f"{_quantity(link_count, 'lien interne', 'liens internes')}, tous résolus")

    if font_count:
        if missing_fonts:
            detail = ", ".join(sorted(missing_fonts)[:4])
            note(
                "warn",
                f"{_quantity(len(missing_fonts), 'police non incorporée', 'polices non incorporées')}"
                f" : {detail}",
            )
        else:
            note("ok", f"{_quantity(font_count, 'police utilisée', 'polices utilisées')}, toutes incorporées")

    if outside:
        note("warn", f"{_quantity(outside, 'fragment de texte', 'fragments de texte')} hors page")
    else:
        note("ok", "Aucun texte hors page")

    if title and author:
        note("info", "Métadonnées : titre et auteur renseignés")
    elif title:
        note("info", "Métadonnées : titre renseigné, auteur absent")
    else:
        note("warn", "Titre absent des métadonnées du PDF")

    if source_images and not images:
        note("warn", f"Aucune des {_number(source_images)} images de l'EPUB n'a été rendue")

    return {"state": state, "pages": len(pages), "checks": checks}


# --------------------------------------------------------------------------- #
# Lecture du PDF
# --------------------------------------------------------------------------- #


def _scan_page(page) -> tuple[str, int]:
    """Extrait le texte d'une page et compte les fragments hors du format."""
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    parts: list[str] = []
    outside = 0

    def visitor(text, cm, tm, _font_dict, _font_size):
        nonlocal outside
        if not text:
            return
        parts.append(text)
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        if x < -1 or x > width + 1 or y < -1 or y > height + 1:
            outside += 1

    page.extract_text(visitor_text=visitor)
    return "".join(parts), outside


def _page_images(page) -> int:
    resources = page.get("/Resources")
    if resources is None:
        return 0
    xobjects = resources.get_object().get("/XObject")
    if xobjects is None:
        return 0
    count = 0
    for reference in xobjects.get_object().values():
        try:
            if reference.get_object().get("/Subtype") == "/Image":
                count += 1
        except Exception:
            continue
    return count


def _fonts(pages) -> tuple[int, set[str]]:
    used: set[str] = set()
    missing: set[str] = set()
    for page in pages:
        resources = page.get("/Resources")
        if resources is None:
            continue
        table = resources.get_object().get("/Font")
        if table is None:
            continue
        for name, reference in table.get_object().items():
            try:
                font = reference.get_object()
            except Exception:
                continue
            label = str(font.get("/BaseFont") or name).lstrip("/")
            used.add(label)
            if not _is_embedded(font):
                missing.add(label)
    return len(used), missing


def _is_embedded(font) -> bool:
    descriptor = font.get("/FontDescriptor")
    if descriptor is not None and _descriptor_embeds(descriptor.get_object()):
        return True
    for descendant in font.get("/DescendantFonts") or []:
        try:
            child = descendant.get_object()
        except Exception:
            continue
        descriptor = child.get("/FontDescriptor")
        if descriptor is not None and _descriptor_embeds(descriptor.get_object()):
            return True
    return False


def _descriptor_embeds(descriptor) -> bool:
    return any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _links(pages, reader, page_ids: set[int]) -> tuple[int, int]:
    """Compte les liens internes et ceux dont la destination n'existe pas."""
    named = reader.named_destinations
    total = 0
    broken = 0
    for page in pages:
        for reference in page.get("/Annots") or []:
            try:
                annot = reference.get_object()
            except Exception:
                continue
            if annot.get("/Subtype") != "/Link":
                continue
            destination = annot.get("/Dest")
            if destination is None:
                action = annot.get("/A")
                if action is None:
                    continue
                action = action.get_object()
                if action.get("/S") != "/GoTo":
                    continue
                destination = action.get("/D")
            if destination is None:
                continue
            total += 1
            if not _resolves(destination, len(pages), page_ids, named):
                broken += 1
    return total, broken


def _resolves(destination, page_count: int, page_ids: set[int], named: dict) -> bool:
    if hasattr(destination, "get_object"):
        destination = destination.get_object()
    if isinstance(destination, bytes):
        destination = destination.decode("latin-1")
    if isinstance(destination, str):
        return destination in named
    try:
        target = destination[0]
    except (TypeError, IndexError, KeyError):
        return False
    if isinstance(target, IndirectObject):
        return target.idnum in page_ids
    try:
        return 0 <= int(target) < page_count
    except (TypeError, ValueError):
        return False


def _count_outline(items) -> int:
    total = 0
    stack = list(items or [])
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        else:
            total += 1
    return total


# --------------------------------------------------------------------------- #
# Comparaison avec la source EPUB
# --------------------------------------------------------------------------- #


def _source_totals(document: EpubDocument | None) -> tuple[int, int]:
    if document is None:
        return 0, 0
    words = 0
    images = 0
    for chapter in document.chapters:
        try:
            tree = lxml.html.document_fromstring(chapter.content)
        except Exception:
            continue
        for tag in ("style", "script", "head"):
            for node in list(tree.iter(tag)):
                node.drop_tree()
        text = " ".join(tree.text_content().split())
        if text:
            words += len(text.split())
        images += len(tree.findall(".//img"))
    return words, images


# --------------------------------------------------------------------------- #
# Mise en forme française
# --------------------------------------------------------------------------- #


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _quantity(value: int, singular: str, plural: str | None = None) -> str:
    word = singular if value == 1 else (plural or f"{singular}s")
    return f"{_number(value)} {word}"


def _page_list(numbers: list[int]) -> str:
    shown = numbers[:_PAGE_LIST_LIMIT]
    label = "p. " + ", ".join(str(number) for number in shown)
    if len(numbers) > len(shown):
        label += "…"
    return label
