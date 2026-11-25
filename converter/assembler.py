"""Composition d'un document HTML unique, prêt pour le rendu WeasyPrint."""

from __future__ import annotations

import base64
import html
import posixpath
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import ebooklib
import lxml.html
from lxml import etree

from .css_guard import DEFAULT_FONT_PT, sanitize_css, sanitize_declarations
from .epub_reader import EpubDocument

_PRINT_CSS = Path(__file__).with_name("print.css").read_text(encoding="utf-8")

_BLOCKED_TAGS = (
    "script",
    "noscript",
    "iframe",
    "frame",
    "frameset",
    "object",
    "embed",
    "form",
    "input",
    "button",
    "textarea",
    "select",
    "option",
    "audio",
    "video",
    "canvas",
    "map",
    "template",
    "base",
)

_IGNORED_PREFIXES = ("data:", "http:", "https:", "mailto:", "tel:", "javascript:", "#", "//")
_URL_IN_CSS = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
_RUNNING_TITLE = re.compile(r"^(?P<head>.+?)\s+-\s+(?P<body>.+?)\s+-\s+.+$")
_CSS_IMPORT = re.compile(r"@import[^;]+;", re.IGNORECASE)

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


# Zones liminaires : pas d'en-tête courant, pas d'entrée dans le sommaire généré.
_FRONT_MATTER = frozenset({"cover", "titlepage", "copyright", "toc"})

# Notes de bas de page : types EPUB 3 lus sur les renvois et sur les blocs de note.
_EPUB_TYPE_ATTR = "{http://www.idpf.org/2007/ops}type"
_NOTEREF_TOKENS = frozenset({"noteref", "doc-noteref"})
_NOTE_TYPE_TOKENS = frozenset({"footnote", "endnote", "rearnote", "note"})
_NOTE_ROLES = frozenset({"doc-footnote", "doc-endnote"})
_ASIDE_NOTE_CLASSES = frozenset({"footnote", "endnote", "note", "fn"})
_BACKLINK_TOKENS = frozenset({"backlink", "doc-backlink"})
_BACKLINK_LABELS = frozenset(
    {"↩", "⤴", "↑", "retour", "back", "retour au texte", "back to text", "retour au corps"}
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
# Une note plus longue resterait une note de fin : elle noierait le bas de page.
_MAX_FOOTNOTE_CHARS = 1200
# Numéro que l'EPUB place parfois dans le texte de la note (« 12. », `<sup>3</sup>`).
_LEADING_NOTE_NUMBER = re.compile(r"^\s*\d{1,3}\s*[.)]\s*")
# Texte toléré dans une section de notes dont toutes les notes passent en bas de page.
_NOTES_LEFTOVER_CHARS = 30

# Éléments qui peuvent vivre dans une note insérée au fil d'un paragraphe.
_INLINE_TAGS = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "br", "cite", "code", "data", "del",
        "dfn", "em", "i", "img", "ins", "kbd", "mark", "q", "rp", "rt", "ruby",
        "s", "samp", "small", "span", "strike", "strong", "sub", "sup", "time",
        "tt", "u", "var", "wbr",
    }
)

_PAGE_FORMATS = {
    "a5": ("A5", 148.0, 210.0),
    "a4": ("A4", 210.0, 297.0),
    "six_by_nine": ("6 × 9 po", 152.4, 228.6),
    "pocket": ("Poche", 110.0, 178.0),
}

_MARGIN_PRESETS = {
    "narrow": (15.0, 13.0, 14.0),
    "normal": (20.0, 17.0, 18.0),
    "wide": (25.0, 22.0, 22.0),
}

DEFAULT_LINE_HEIGHT = 1.55
_FONT_SIZES = (10.0, DEFAULT_FONT_PT, 12.0)
_LINE_HEIGHTS = (1.4, DEFAULT_LINE_HEIGHT, 1.7)


@dataclass(frozen=True)
class Options:
    include_toc: bool = True
    include_page_numbers: bool = True
    include_cover_page: bool = True
    page_format: str = "a5"
    margins: str = "normal"
    font_size: float = DEFAULT_FONT_PT
    line_height: float = DEFAULT_LINE_HEIGHT


def build_document(document: EpubDocument, media_root: Path, options: Options) -> str:
    """Assemble les chapitres, le sommaire et les pages liminaires en un seul HTML."""
    dropped = _dropped_chapters(document, options)
    prepared, css_blocks = _prepare_chapters(document, media_root, dropped)
    # Sommaire de l'EPUB inutilisable : des signets sont générés depuis les titres détectés.
    toc_broken = _toc_targets_broken(document, prepared)

    parts: list[str] = []
    if options.include_cover_page and document.cover_bytes:
        parts.append(_cover_page(document))
    if options.include_cover_page:
        parts.append(_title_page(document))
    if options.include_toc:
        toc = _toc_page(document, prepared)
        if toc:
            parts.append(toc)

    parts.append('<div class="content">')
    for chapter, body, _ids in prepared:
        title = html.escape(chapter.title or f"Chapitre {chapter.index + 1}")
        running = "" if chapter.kind in _FRONT_MATTER else html.escape(_running_title(chapter.title))
        bookmark = ""
        if toc_broken and chapter.kind not in _FRONT_MATTER and not _has_heading(body):
            # Aucun titre HTML ne produira de signet : on en pose un sur le titre détecté.
            bookmark = f'<div class="reliure-bookmark" data-label="{title}"></div>'
        parts.append(
            f'<section class="chapter" id="{chapter.anchor}" data-title="{title}" data-kind="{chapter.kind}">'
            f'<span class="running-head">{running}</span>'
            f"{bookmark}"
            f"{_inner_html(body)}"
            "</section>"
        )
    parts.append("</div>")

    return _wrap_document(document, options, "".join(parts), css_blocks)


# --------------------------------------------------------------------------- #
# Chapitres
# --------------------------------------------------------------------------- #


def _prepare_chapters(document: EpubDocument, media_root: Path, dropped: dict[int, str]):
    prepared: list[tuple] = []
    css_blocks: list[str] = []
    seen_css: set[str] = set()

    for chapter in document.chapters:
        if chapter.index in dropped:
            continue
        tree = _parse(chapter.content)
        if tree is None:
            continue
        body = tree.find("body")
        if body is None:
            body = tree
        _collect_styles(tree, chapter.name, media_root, css_blocks, seen_css)
        _strip_blocked(body)
        _rewrite_resources(body, chapter.name, media_root)
        _clean_inline_styles(body)
        _space_heading_breaks(body)
        ids = _rename_ids(body, chapter)
        prepared.append((chapter, body, ids))

    _append_orphan_styles(document, media_root, css_blocks, seen_css)

    # Deuxième passe : les liens internes ont besoin de tous les identifiants.
    id_maps = {chapter.index: ids for chapter, _body, ids in prepared}
    lookup = _chapter_lookup(document)
    consumed, note_ids = _extract_footnotes(prepared, lookup, id_maps)
    _drop_emptied_notes(prepared, consumed, dropped)
    for chapter, body, ids in prepared:
        _rewrite_links(body, chapter, lookup, ids, id_maps, dropped, note_ids)

    return prepared, css_blocks


def _parse(content: bytes):
    parser = lxml.html.HTMLParser(recover=True, remove_comments=True, encoding="utf-8")
    try:
        tree = lxml.html.document_fromstring(content, parser=parser)
    except (etree.ParserError, ValueError):
        return None
    return tree if tree is not None else None


def _strip_blocked(root) -> None:
    for tag in _BLOCKED_TAGS:
        for element in list(root.xpath(f".//{tag}")):
            element.drop_tree()


def _collect_styles(root, base_name: str, media_root: Path, css_blocks, seen_css) -> None:
    for link in list(root.xpath(".//link")):
        rel = (link.get("rel") or "").lower()
        href = link.get("href") or ""
        link.drop_tree()
        if "stylesheet" not in rel or not href:
            continue
        target = _resolve(media_root, base_name, href)
        if target is None or str(target) in seen_css:
            continue
        seen_css.add(str(target))
        text = _read_text(target)
        if text.strip():
            css_blocks.append(_rewrite_css(text, _relative_name(media_root, target), media_root))

    for style in list(root.xpath(".//style")):
        text = style.text_content() or ""
        style.drop_tree()
        if text.strip():
            css_blocks.append(_rewrite_css(text, base_name, media_root))


def _append_orphan_styles(document: EpubDocument, media_root: Path, css_blocks, seen_css) -> None:
    """Ajoute les feuilles de style de l'EPUB qu'aucun chapitre ne référence."""
    for item in document.book.get_items_of_type(ebooklib.ITEM_STYLE):
        name = posixpath.normpath(item.get_name() or "")
        target = _safe_media_path(media_root, name)
        if target is None or str(target) in seen_css:
            continue
        text = _read_text(target)
        if not text.strip():
            continue
        seen_css.add(str(target))
        css_blocks.append(_rewrite_css(text, name, media_root))


def _rename_ids(root, chapter) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        old = element.get("id")
        if not old:
            continue
        new = f"{chapter.anchor}-{old}"
        mapping[old] = new
        element.set("id", new)
    return mapping


def _rewrite_resources(root, base_name: str, media_root: Path) -> None:
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        tag = element.tag.lower()

        if tag in ("img", "source"):
            element.attrib.pop("srcset", None)
            element.attrib.pop("sizes", None)
            src = element.get("src")
            if not src:
                continue
            target = _resolve(media_root, base_name, src)
            if target is None:
                if tag == "img":
                    element.drop_tree()
                else:
                    element.attrib.pop("src", None)
                continue
            element.set("src", target.as_uri())

        elif tag in ("image", "use"):
            for attribute in ("href", _XLINK_HREF):
                value = element.get(attribute)
                if not value or value.startswith("#"):
                    continue
                target = _resolve(media_root, base_name, value)
                if target is not None:
                    element.set(attribute, target.as_uri())


def _clean_inline_styles(root) -> None:
    """Neutralise les styles en ligne dangereux (position, sauts de page, colonnes)."""
    for element in list(root.iter()):
        if not isinstance(element.tag, str):
            continue
        style = element.get("style")
        if not style:
            continue
        cleaned = sanitize_declarations(style)
        if cleaned:
            element.set("style", cleaned)
        else:
            element.attrib.pop("style", None)


def _space_heading_breaks(root) -> None:
    """Un <br> dans un titre colle les mots dans les signets : on rétablit l'espace."""
    for heading in root.iter("h1", "h2", "h3", "h4", "h5", "h6"):
        for br in heading.iter("br"):
            tail = br.tail or ""
            if tail and not tail[0].isspace():
                br.tail = " " + tail


def _rewrite_links(
    root,
    chapter,
    lookup,
    own_ids: dict[str, str],
    id_maps: dict[int, dict[str, str]],
    dropped: dict[int, str],
    note_ids: dict[str, str],
) -> None:
    for anchor in root.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        if href.startswith("#"):
            fragment = href[1:]
            target_id = note_ids.get(own_ids.get(fragment) or fragment)
            if target_id is None:
                target_id = own_ids.get(fragment)
            if target_id:
                anchor.set("href", f"#{target_id}")
            continue
        if href.startswith(_IGNORED_PREFIXES):
            continue
        path_part, _, fragment = href.partition("#")
        target = lookup(path_part) if path_part else chapter
        if target is None:
            continue
        if target.index in dropped:
            replacement = dropped[target.index]
            if replacement:
                anchor.set("href", f"#{replacement}")
            else:
                anchor.attrib.pop("href", None)
            continue
        anchor_id = target.anchor
        if fragment:
            mapped = id_maps.get(target.index, {}).get(fragment)
            if mapped:
                anchor_id = note_ids.get(mapped, mapped)
        anchor.set("href", f"#{anchor_id}")


def _inner_html(element) -> str:
    parts = [element.text or ""]
    for child in element:
        parts.append(lxml.html.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Notes de bas de page
# --------------------------------------------------------------------------- #


def _extract_footnotes(prepared, lookup, id_maps) -> tuple[dict[int, int], dict[str, str]]:
    """Compose les notes de l'EPUB en bas de page.

    Renvoie le nombre de notes reprises par chapitre et la correspondance entre
    l'identifiant d'origine d'une note et celui de sa note de bas de page : les renvois
    d'une note à l'autre doivent aboutir.

    Trois signaux sont reconnus : les renvois `epub:type="noteref"`, tout lien interne
    qui pointe vers une note typée ou vers une section de notes, et les `aside` typés
    restés dans le texte. Le numéro est posé par Reliure pour que le renvoi mène à la
    note et que la note revienne à l'appel.
    """
    elements: dict[str, tuple] = {}
    for _chapter, body, _ids in prepared:
        for element in body.iter():
            if isinstance(element.tag, str) and element.get("id"):
                elements[element.get("id")] = (_chapter, element)

    consumed: dict[int, int] = {}
    note_ids: dict[str, str] = {}
    number = 0

    for chapter, body, _ids in prepared:
        for anchor in list(body.iter("a")):
            target = _note_target(anchor, chapter, lookup, id_maps, elements)
            if target is None:
                continue
            owner, element = target
            _drop_backlinks(element)
            _strip_note_number(element)
            text = _plain_text(element)
            if not text or len(text) > _MAX_FOOTNOTE_CHARS:
                continue
            number += 1
            call = _make_call(anchor, number)
            note = _make_note(number, _inline_markup(element), call.get("id"))
            # Le texte qui suit l'appel reste après la note : la note doit flotter là où
            # se trouve le renvoi, pas à la fin du paragraphe (donc de la page).
            following = call.tail
            call.tail = None
            call.addnext(note)
            note.tail = following
            note_id = element.get("id") or ""
            note_ids[note_id] = f"reliure-fn-{number}"
            element.drop_tree()
            elements.pop(note_id, None)
            consumed[owner.index] = consumed.get(owner.index, 0) + 1

    # `aside` typés sans renvoi : la note est déjà à sa place dans le texte.
    for chapter, body, _ids in prepared:
        if chapter.kind == "notes":
            continue
        for element in list(body.iter()):
            if not _is_inline_note(element):
                continue
            _drop_backlinks(element)
            _strip_note_number(element)
            text = _plain_text(element)
            if not text or len(text) > _MAX_FOOTNOTE_CHARS:
                continue
            number += 1
            element.attrib.pop("style", None)  # un `display: none` masquerait la note
            _collapse_whitespace(element)
            _prepend_note_number(element, number)
            if element.get("id"):
                note_ids[element.get("id")] = f"reliure-fn-{number}"
            element.set("class", "footnote reliure-note")
            element.set("id", f"reliure-fn-{number}")
            call = _make_call(None, number)
            element.addprevious(call)
            _append_backlink(element, call.get("id"))
            consumed[chapter.index] = consumed.get(chapter.index, 0) + 1

    return consumed, note_ids


def _note_target(anchor, chapter, lookup, id_maps, elements):
    """Élément visé par un renvoi de note, ou `None` pour un simple lien interne."""
    if _inside_nav(anchor):
        return None
    href = (anchor.get("href") or "").strip()
    if not href:
        return None
    if href.startswith("#"):
        path, fragment = "", href[1:]
    else:
        if href.startswith(_IGNORED_PREFIXES):
            return None
        path, _, fragment = href.partition("#")
    if not fragment:
        return None
    target_chapter = lookup(path) if path else chapter
    if target_chapter is None:
        return None
    new_id = id_maps.get(target_chapter.index, {}).get(fragment)
    entry = elements.get(new_id) if new_id else None
    if entry is None:
        return None
    owner, element = entry
    if element is anchor or any(node is element for node in anchor.iterancestors()):
        return None
    if _is_noteref(anchor):
        return owner, element
    if owner.kind == "notes" or _is_note_holder(element):
        return owner, element
    return None


def _make_call(anchor, number: int):
    """Renvoi cliquable : reprend le lien existant ou en crée un nouveau.

    L'identifiant d'origine du renvoi est conservé : les notes de l'EPUB renvoient
    parfois d'une note à l'autre, et ces liens doivent continuer à aboutir.
    """
    attributes = {"class": "fn-call", "href": f"#reliure-fn-{number}"}
    if anchor is None:
        call = etree.Element("a", attributes)
        call.set("id", f"reliure-fnref-{number}")
    else:
        call = anchor
        previous_id = call.get("id") or f"reliure-fnref-{number}"
        call.attrib.clear()
        call.attrib.update(attributes)
        call.set("id", previous_id)
        for child in list(call):
            call.remove(child)
    call.text = str(number)
    return call


def _make_note(number: int, content: str, call_id: str):
    """Note en ligne : elle reste dans le paragraphe et flotte en bas de page."""
    markup = (
        f'<span class="footnote reliure-note" id="reliure-fn-{number}">'
        f'<span class="fn-num">{number}.</span> {content}'
        f'<a class="fn-back" href="#{call_id}"> ↩</a></span>'
    )
    return lxml.html.fromstring(markup)


def _strip_note_number(element) -> None:
    """Retire le numéro que l'EPUB place dans le texte de la note.

    Les sections de notes numérotent souvent leurs entrées par chapitre (« 1. » pour la
    première note du chapitre) alors que Reliure numérote tout le livre. Le numéro est
    tantôt du texte, tantôt un lien ou un exposant suivi d'un point.
    """
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        text = node.text or ""
        if not text.strip():
            continue
        if _LEADING_NOTE_NUMBER.match(text):
            node.text = _LEADING_NOTE_NUMBER.sub("", text, count=1)
            return
        if not re.fullmatch(r"\s*\d{1,3}\s*", text):
            return
        tail = node.tail or ""
        if node.tag.lower() != "sup" and not re.match(r"^\s*[.)]", tail):
            return
        node.tail = re.sub(r"^\s*[.)]\s*", "", tail, count=1)
        node.drop_tree()
        return


def _prepend_note_number(element, number: int) -> None:
    marker = etree.Element("span", {"class": "fn-num"})
    marker.text = f"{number}."
    marker.tail = element.text
    element.text = None
    element.insert(0, marker)


def _append_backlink(element, call_id: str) -> None:
    back = etree.SubElement(element, "a", {"class": "fn-back", "href": f"#{call_id}"})
    back.text = " ↩"


def _drop_emptied_notes(prepared, consumed: dict[int, int], dropped: dict[int, str]) -> None:
    """Retire une section de notes dont toutes les notes passent en bas de page."""
    for entry in list(prepared):
        chapter, body, _ids = entry
        if chapter.kind != "notes" or not consumed.get(chapter.index):
            continue
        if len(prepared) <= 1 or _remaining_text_length(body) > _NOTES_LEFTOVER_CHARS:
            continue
        prepared.remove(entry)
        dropped[chapter.index] = ""


def _remaining_text_length(body) -> int:
    """Caractères restants hors titres : sert à repérer une section de notes vidée."""
    parts: list[str] = [body.text or ""]
    for element in body.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag.lower() in _HEADING_TAGS or any(
            isinstance(node.tag, str) and node.tag.lower() in _HEADING_TAGS
            for node in element.iterancestors()
        ):
            continue
        parts.append(element.text or "")
        parts.append(element.tail or "")
    return len("".join(" ".join("".join(parts).split())))


def _inline_markup(element) -> str:
    """Contenu d'une note ramené à des éléments en ligne.

    Une note est insérée au fil du paragraphe qui l'appelle : un bloc imbriqué
    (`<p>`, `<div>`…) y fermerait le paragraphe en deux morceaux. Les blocs sont donc
    dépliés, leur contenu étant simplement séparé par des espaces.
    """
    parts: list[str] = [html.escape(_collapse_whitespace_text(element.text))]
    for child in element:
        if not isinstance(child.tag, str):
            continue
        if child.tag.lower() in _INLINE_TAGS:
            parts.append(etree.tostring(child, encoding="unicode", method="html", with_tail=False))
        else:
            parts.extend((" ", _inline_markup(child), " "))
        parts.append(html.escape(_collapse_whitespace_text(child.tail)))
    return "".join(parts).strip()


def _collapse_whitespace_text(value: str | None) -> str:
    """Compacte les blancs d'un nœud texte.

    Un saut de ligne dans une note fait échouer la composition de l'area `@footnote`
    (assertion interne de WeasyPrint sur les coupures de ligne).
    """
    if not value:
        return ""
    text = " ".join(value.split())
    if not text:
        return " "
    return (" " if value[0].isspace() else "") + text + (" " if value[-1].isspace() else "")


def _collapse_whitespace(root) -> None:
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if element.text:
            element.text = _collapse_whitespace_text(element.text)
        if element.tail:
            element.tail = _collapse_whitespace_text(element.tail)


def _plain_text(element) -> str:
    return " ".join((element.text_content() or "").split())


def _drop_backlinks(root) -> None:
    """Retire les retours déjà posés par l'EPUB : Reliure ajoute le sien."""
    for anchor in list(root.iter("a")):
        label = " ".join((anchor.text_content() or "").split()).lower()
        if _type_tokens(_type_attr(anchor)) & _BACKLINK_TOKENS or label in _BACKLINK_LABELS:
            anchor.drop_tree()


def _is_noteref(anchor) -> bool:
    if _type_tokens(_type_attr(anchor)) & _NOTEREF_TOKENS:
        return True
    return (anchor.get("role") or "").strip().lower() in _NOTEREF_TOKENS


def _is_note_holder(element) -> bool:
    for node in [element, *element.iterancestors()]:
        if not isinstance(node.tag, str):
            continue
        if _type_tokens(_type_attr(node)) & _NOTE_TYPE_TOKENS:
            return True
        if (node.get("role") or "").strip().lower() in _NOTE_ROLES:
            return True
    return False


def _is_inline_note(element) -> bool:
    """`aside` typé note resté dans le texte, hors section de notes."""
    if not isinstance(element.tag, str) or element.tag.lower() != "aside":
        return False
    classes = (element.get("class") or "").lower().split()
    if "reliure-note" in classes:
        return False
    if _type_tokens(_type_attr(element)) & _NOTE_TYPE_TOKENS:
        return True
    if (element.get("role") or "").strip().lower() in _NOTE_ROLES:
        return True
    return bool(_ASIDE_NOTE_CLASSES.intersection(classes))


def _inside_nav(element) -> bool:
    return any(
        isinstance(node.tag, str) and node.tag.lower() == "nav"
        for node in element.iterancestors()
    )


def _type_attr(element) -> str:
    return element.get("epub:type") or element.get(_EPUB_TYPE_ATTR) or ""


def _type_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.split(r"\s+", value or "") if token}


# --------------------------------------------------------------------------- #
# Pages liminaires et sommaire
# --------------------------------------------------------------------------- #


def _cover_page(document: EpubDocument) -> str:
    media_type = document.cover_media_type or "image/jpeg"
    encoded = base64.b64encode(document.cover_bytes or b"").decode("ascii")
    alt = html.escape(document.title or "Couverture")
    return (
        '<section class="cover-page" id="reliure-cover">'
        f'<img src="data:{media_type};base64,{encoded}" alt="{alt}">'
        "</section>"
    )


def _title_page(document: EpubDocument) -> str:
    facts: list[tuple[str, str]] = []
    if document.publisher:
        facts.append(("Éditeur", document.publisher))
    if document.date:
        facts.append(("Édition", _french_date(document.date)))
    if document.language:
        facts.append(("Langue", document.language_label))
    facts.append(("Source", document.source_name))
    facts.append(("Conversion", date.today().strftime("%d/%m/%Y")))

    fact_html = "".join(
        f"<div><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>"
        for label, value in facts
    )
    return (
        '<section class="title-page" id="reliure-title">'
        '<p class="title-kicker">Édition numérique</p>'
        f'<h1 class="title-main">{html.escape(document.title or "Sans titre")}</h1>'
        f'<p class="title-author">{html.escape(document.author or "Auteur inconnu")}</p>'
        '<div class="title-rule"></div>'
        f'<dl class="title-facts">{fact_html}</dl>'
        "</section>"
    )


def _running_title(title: str) -> str:
    """En-tête courant : le titre du chapitre sans son sous-titre, pour tenir sur une ligne."""
    value = (title or "").strip()
    match = _RUNNING_TITLE.match(value)
    if not match:
        return value
    return f"{match.group('head').strip()} - {match.group('body').strip()}"


def _french_date(value: str) -> str:
    """Transforme une date ISO en date lisible à la française."""
    raw = (value or "").strip()
    if not raw:
        return ""
    moment = None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = datetime.strptime(raw[:10], "%Y-%m-%d")
        except ValueError:
            return raw
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"


def _has_heading(body) -> bool:
    """Vrai si un titre HTML du chapitre produira déjà un signet (`bookmark-level`)."""
    return any(body.xpath(f".//{tag}") for tag in ("h1", "h2", "h3", "h4", "h5", "h6"))


def _toc_targets_broken(document: EpubDocument, prepared) -> bool:
    """Vrai si au moins une entrée du sommaire de l'EPUB vise une cible inexistante."""
    id_sets = {chapter.index: set(ids) for chapter, _body, ids in prepared}
    lookup = _chapter_lookup(document)
    stack = list(document.toc)
    while stack:
        link = stack.pop()
        stack.extend(link.children)
        if not link.target:
            continue
        chapter = lookup(link.target)
        if chapter is None or chapter.index not in id_sets:
            return True
        if link.fragment and link.fragment not in id_sets[chapter.index]:
            return True
    return False


def _toc_page(document: EpubDocument, prepared) -> str:
    id_sets = {chapter.index: set(ids) for chapter, _body, ids in prepared}
    lookup = _chapter_lookup(document)
    nodes = _build_toc_nodes(document.toc, lookup, id_sets)

    if not nodes:
        nodes = [
            {"title": chapter.title or f"Chapitre {index + 1}", "target": chapter.anchor, "children": []}
            for index, chapter in enumerate(document.chapters)
            if chapter.index in id_sets and chapter.kind not in _FRONT_MATTER
        ]
    if not nodes:
        return ""
    return (
        '<section class="toc-page" id="reliure-toc">'
        '<h1 class="toc-title">Sommaire</h1>'
        f"{_toc_markup(nodes, 1)}"
        "</section>"
    )


def _build_toc_nodes(links, lookup, id_sets) -> list[dict]:
    nodes: list[dict] = []
    for link in links:
        chapter = lookup(link.target) if link.target else None
        children = _build_toc_nodes(link.children, lookup, id_sets)
        if chapter is None:
            if children and link.title:
                # Regroupement sans page cible (partie de livre).
                nodes.append({"title": link.title, "target": None, "children": children})
            else:
                nodes.extend(children)
            continue
        if chapter.kind in _FRONT_MATTER or chapter.index not in id_sets:
            continue
        target = chapter.anchor
        if link.fragment and link.fragment in id_sets.get(chapter.index, set()):
            target = f"{chapter.anchor}-{link.fragment}"
        nodes.append(
            {
                "title": link.title or chapter.title,
                "target": target,
                "children": children,
            }
        )
    return nodes


def _toc_markup(nodes: list[dict], level: int) -> str:
    if not nodes:
        return ""
    depth = min(level, 3)
    items = []
    for node in nodes:
        children = _toc_markup(node["children"], level + 1)
        target = node.get("target")
        if target:
            label = f'<a href="#{target}">{html.escape(node["title"])}</a>'
        else:
            label = f'<span class="toc-part-label">{html.escape(node["title"])}</span>'
        leaf = "" if children else " toc-leaf"
        items.append(f'<li class="toc-item toc-level-{depth}{leaf}">{label}{children}</li>')
    return f'<ul class="toc-list">{"".join(items)}</ul>'


# --------------------------------------------------------------------------- #
# Structure et mise en page
# --------------------------------------------------------------------------- #


def _dropped_chapters(document: EpubDocument, options: Options) -> dict[int, str]:
    """Documents internes remplacés par une page générée (couverture, titre, sommaire)."""
    dropped: dict[int, str] = {}
    for chapter in document.chapters:
        if chapter.kind == "cover" and options.include_cover_page and document.cover_bytes:
            dropped[chapter.index] = "reliure-cover"
        elif chapter.kind == "titlepage" and options.include_cover_page:
            dropped[chapter.index] = "reliure-title"
        elif chapter.kind == "toc" and options.include_toc:
            dropped[chapter.index] = "reliure-toc"
    return dropped


def _layout_css(options: Options) -> str:
    _label, width, height = _PAGE_FORMATS.get(options.page_format, _PAGE_FORMATS["a5"])
    top, side, bottom = _MARGIN_PRESETS.get(options.margins, _MARGIN_PRESETS["normal"])
    font_size = options.font_size if options.font_size in _FONT_SIZES else DEFAULT_FONT_PT
    line_height = options.line_height if options.line_height in _LINE_HEIGHTS else DEFAULT_LINE_HEIGHT
    return (
        f"@page {{ size: {width:g}mm {height:g}mm; margin: {top:g}mm {side:g}mm {bottom:g}mm; }}\n"
        f"@page cover {{ size: {width:g}mm {height:g}mm; }}\n"
        f".cover-page {{ width: {width:g}mm; height: {height:g}mm; }}\n"
        # `!important` : une déclaration de l'EPUB marquée ainsi ne doit pas reprendre
        # le dessus sur le corps et l'interligne choisis.
        f"html, body {{ font-size: {font_size:g}pt !important; }}\n"
        f"body {{ line-height: {line_height:g} !important; }}\n"
        f".title-page {{ padding-top: {height * 0.2:g}mm; }}\n"
    )


# --------------------------------------------------------------------------- #
# Document final
# --------------------------------------------------------------------------- #


def _wrap_document(document: EpubDocument, options: Options, body: str, css_blocks: list[str]) -> str:
    lang = (document.language or "fr").replace("_", "-").split("-")[0].lower() or "fr"
    print_css = _PRINT_CSS + _layout_css(options)
    if not options.include_page_numbers:
        print_css += (
            "\n@page { @top-center { content: none; } @bottom-center { content: none; } }\n"
            "@page front { @top-center { content: none; } @bottom-center { content: none; } }\n"
        )

    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{html.escape(lang)}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(document.title or 'Document')}</title>\n"
        f'<meta name="author" content="{html.escape(document.author or "")}">\n'
        f'<meta name="description" content="Converti depuis {html.escape(document.source_name)} avec Reliure">\n'
        '<meta name="keywords" content="EPUB, PDF, livre numérique">\n'
        '<meta name="generator" content="Reliure 1.0">\n'
        f'<meta name="dcterms.created" content="{date.today().isoformat()}">\n'
        f"<style>\n{chr(10).join(css_blocks)}\n</style>\n"
        f"<style>\n{print_css}\n</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- #
# Utilitaires fichiers et URL
# --------------------------------------------------------------------------- #


def _chapter_lookup(document: EpubDocument):
    by_name: dict[str, object] = {}
    for chapter in document.chapters:
        name = posixpath.normpath(chapter.name)
        by_name[name] = chapter
        by_name.setdefault(name.lower(), chapter)
        by_name.setdefault(posixpath.basename(name).lower(), chapter)

    def lookup(path: str):
        if not path:
            return None
        key = posixpath.normpath(unquote(path).lstrip("/"))
        return (
            by_name.get(key)
            or by_name.get(key.lower())
            or by_name.get(posixpath.basename(key).lower())
        )

    return lookup


def _resolve(media_root: Path, base_name: str, url: str) -> Path | None:
    url = (url or "").strip()
    if not url or url.startswith(_IGNORED_PREFIXES):
        return None
    clean = unquote(url.split("#")[0].split("?")[0]).strip()
    if not clean:
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base_name), clean.lstrip("/")))
    if joined.startswith("../"):
        return None
    try:
        candidate = (media_root / joined).resolve()
        if not candidate.is_relative_to(media_root.resolve()) or not candidate.is_file():
            return None
        return candidate
    except (OSError, ValueError):
        return None


def _safe_media_path(media_root: Path, name: str) -> Path | None:
    try:
        candidate = (media_root / Path(*PurePosixPath(unquote(name)).parts)).resolve()
    except (OSError, ValueError):
        return None
    try:
        if not candidate.is_relative_to(media_root.resolve()) or not candidate.is_file():
            return None
    except OSError:
        return None
    return candidate


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _relative_name(media_root: Path, target: Path) -> str:
    try:
        return target.relative_to(media_root.resolve()).as_posix()
    except ValueError:
        return target.name


def _rewrite_css(css_text: str, base_name: str, media_root: Path) -> str:
    def replace(match: re.Match) -> str:
        url = match.group(2).strip()
        if url.startswith(_IGNORED_PREFIXES):
            return match.group(0)
        target = _resolve(media_root, base_name, url)
        if target is None:
            return match.group(0)
        return f'url("{target.as_uri()}")'

    css_text = _URL_IN_CSS.sub(replace, css_text)
    css_text = _CSS_IMPORT.sub("", css_text)
    return sanitize_css(css_text)
