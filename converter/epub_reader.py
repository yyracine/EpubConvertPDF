"""Lecture d'un EPUB avec EbookLib : métadonnées, chapitres, sommaire, couverture."""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import ebooklib
import lxml.html
from ebooklib import epub

from .css_guard import sanitize_css, scan_css
from .errors import ConversionError

MAX_EPUB_BYTES = 100 * 1024 * 1024
_FONT_SUFFIXES = (".otf", ".ttf", ".woff", ".woff2", ".ttc", ".otc")
_HEADING_TAGS = ("h1", "h2", "h3")
_EXTERNAL_PREFIXES = ("http:", "https:", "mailto:", "tel:", "javascript:", "data:", "//")
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_MIN_TEXT_WORDS = 20

_EPUB_TYPE_ATTR = "{http://www.idpf.org/2007/ops}type"

# Seuls ces éléments portent un type qui décrit le document entier. Un `epub:type`
# posé sur un lien ou une portée en ligne décrit sa cible, pas le fichier qui le contient.
_DOCUMENT_LEVEL_TAGS = frozenset(
    {"html", "body", "main", "section", "article", "nav", "header", "div"}
)

# Types structurels (EPUB 3, guide EPUB 2) ramenés à nos zones éditoriales.
_KIND_TOKENS = (
    ("cover", frozenset({"cover"})),
    ("titlepage", frozenset({"titlepage", "title-page", "halftitle", "half-title"})),
    ("copyright", frozenset({"copyright-page", "copyright", "colophon", "imprint"})),
    ("toc", frozenset({"toc"})),
    ("notes", frozenset({"endnotes", "footnotes", "rearnotes", "notes", "apparatus"})),
    ("index", frozenset({"index"})),
)

# Codes ISO 639-2 (parfois utilisés par les EPUB) ramenés au code à deux lettres.
_LANGUAGE_ALIASES = {
    "fra": "fr",
    "fre": "fr",
    "eng": "en",
    "spa": "es",
    "deu": "de",
    "ger": "de",
    "ita": "it",
    "por": "pt",
    "nld": "nl",
    "dut": "nl",
    "rus": "ru",
    "jpn": "ja",
    "zho": "zh",
    "chi": "zh",
    "ara": "ar",
}

LANGUAGE_LABELS = {
    "fr": "Français",
    "en": "Anglais",
    "es": "Espagnol",
    "de": "Allemand",
    "it": "Italien",
    "pt": "Portugais",
    "nl": "Néerlandais",
    "ru": "Russe",
    "ja": "Japonais",
    "zh": "Chinois",
    "ar": "Arabe",
}


@dataclass
class Chapter:
    index: int
    name: str
    title: str
    content: bytes
    kind: str = "body"

    @property
    def anchor(self) -> str:
        return f"c{self.index}"


@dataclass
class TocLink:
    title: str
    target: str
    fragment: str = ""
    children: list["TocLink"] = field(default_factory=list)


@dataclass
class EpubDocument:
    path: Path
    book: epub.EpubBook
    source_name: str
    title: str
    author: str
    publisher: str
    language: str
    description: str
    date: str
    identifier: str
    cover_bytes: bytes | None
    cover_media_type: str | None
    chapters: list[Chapter]
    toc: list[TocLink]
    warnings: list[str]
    fixed_layout: bool
    obfuscated_fonts: bool

    @property
    def language_label(self) -> str:
        code = (self.language or "").replace("_", "-").split("-")[0].lower()
        code = _LANGUAGE_ALIASES.get(code, code)
        if not code:
            return "Non renseignée"
        label = LANGUAGE_LABELS.get(code)
        if not label:
            return self.language
        return f"{label} ({code})"

    def summary(self) -> dict:
        return {
            "title": self.title,
            "author": self.author,
            "publisher": self.publisher,
            "language": self.language,
            "language_label": self.language_label,
            "date": self.date,
            "chapters": len(self.chapters),
            "warnings": self.warnings,
            "fixed_layout": self.fixed_layout,
            "has_cover": self.cover_bytes is not None,
            "has_toc": bool(self.toc),
        }


# --------------------------------------------------------------------------- #
# Ouverture et contrôles
# --------------------------------------------------------------------------- #


def read_book(path: Path, source_name: str | None = None) -> EpubDocument:
    """Ouvre un EPUB et prépare tout ce qui est nécessaire à la conversion."""
    obfuscated_fonts = _ensure_readable(path)

    try:
        book = epub.read_epub(str(path))
    except Exception as exc:  # EbookLib peut lever des erreurs très variées
        raise ConversionError(
            "Impossible d'ouvrir cet EPUB : le fichier est peut-être endommagé.",
            hint=f"Détail technique : {type(exc).__name__}.",
            status=422,
        ) from exc

    title = _dc_meta(book, "title") or Path(source_name or path.name).stem
    author = _dc_meta(book, "creator")
    publisher = _dc_meta(book, "publisher")
    language = _dc_meta(book, "language")
    description = _dc_meta(book, "description")
    identifier = _dc_meta(book, "identifier")
    date = _dc_meta(book, "date")

    toc = _walk_toc(book.toc)
    toc_titles = _toc_title_index(toc)
    items = _chapter_items(book)
    structural = _structural_signals(book)

    chapters = []
    for index, item in enumerate(items):
        name = item.get_name()
        chapter_title = _chapter_title(item, toc_titles, index)
        content = _item_bytes(item)
        signals = structural.get(_signal_key(name), set()) | _content_types(content)
        chapters.append(
            Chapter(
                index=index,
                name=name,
                title=chapter_title,
                content=content,
                kind=_classify(name, chapter_title, signals),
            )
        )

    if not chapters:
        raise ConversionError(
            "Aucun chapitre exploitable n'a été trouvé dans cet EPUB.",
            hint="Le fichier ne contient pas de contenu XHTML lisible.",
            status=422,
        )

    cover_bytes, cover_media_type = _cover(book)
    fixed_layout = _is_fixed_layout(book)
    warnings = _warnings(title, author, cover_bytes, toc, fixed_layout, obfuscated_fonts)

    return EpubDocument(
        path=path,
        book=book,
        source_name=source_name or path.name,
        title=title,
        author=author,
        publisher=publisher,
        language=language,
        description=description,
        date=date,
        identifier=identifier,
        cover_bytes=cover_bytes,
        cover_media_type=cover_media_type,
        chapters=chapters,
        toc=toc,
        warnings=warnings,
        fixed_layout=fixed_layout,
        obfuscated_fonts=obfuscated_fonts,
    )


def extract_resources(document: EpubDocument, destination: Path) -> None:
    """Écrit toutes les ressources de l'EPUB sur disque, à leurs chemins d'origine."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for item in document.book.get_items():
        relative = _safe_relative(item.get_name() or "")
        if relative is None:
            continue
        try:
            content = item.get_content()
        except Exception:
            continue
        if not content:
            continue
        target = destination / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.resolve().is_relative_to(root):
                continue
            target.write_bytes(content)
        except OSError:
            continue


def _ensure_readable(path: Path) -> bool:
    """Valide le conteneur EPUB ; indique si des polices sont obfusquées."""
    if not path.is_file():
        raise ConversionError("Le fichier reçu est introuvable.")
    size = path.stat().st_size
    if size == 0:
        raise ConversionError("Le fichier est vide.", hint="Choisissez un autre EPUB.")
    if size > MAX_EPUB_BYTES:
        raise ConversionError(
            f"Le fichier dépasse la limite de {MAX_EPUB_BYTES // (1024 * 1024)} Mo.",
            status=413,
        )
    if not zipfile.is_zipfile(path):
        raise ConversionError(
            "Ce fichier n'est pas un EPUB valide.",
            hint="Vérifiez qu'il s'agit bien d'un fichier .epub et non d'un fichier renommé.",
        )
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "META-INF/container.xml" not in names:
            raise ConversionError(
                "Ce fichier n'a pas la structure d'un EPUB (container.xml manquant).",
                hint="Le fichier est peut-être un autre format renommé en .epub.",
            )
        if "META-INF/encryption.xml" in names:
            return _reject_drm(archive.read("META-INF/encryption.xml"))
    return False


def _reject_drm(raw: bytes) -> bool:
    text = raw.decode("utf-8", "replace")
    references = re.findall(r"<CipherReference[^>]*URI=[\"']([^\"']+)[\"']", text)
    fonts_only = bool(references) and all(
        unquote(ref).lower().split("?")[0].endswith(_FONT_SUFFIXES) for ref in references
    )
    if fonts_only:
        # Obfuscation de polices : autorisée par la spécification, le texte reste lisible.
        return True
    raise ConversionError(
        "Cet EPUB est protégé par un verrou numérique (DRM) : il ne peut pas être converti.",
        hint="Utilisez un EPUB sans DRM, par exemple un livre libre de droits ou un fichier exporté sans protection.",
        status=422,
    )


def _dc_meta(book: epub.EpubBook, name: str) -> str:
    try:
        values = book.get_metadata("DC", name)
    except Exception:
        return ""
    for value, _attributes in values:
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


# --------------------------------------------------------------------------- #
# Chapitres
# --------------------------------------------------------------------------- #


def _media_type(item) -> str:
    try:
        value = item.get_media_type()
    except AttributeError:
        value = getattr(item, "media_type", "")
    return str(value or "")


def _properties(item) -> set[str]:
    try:
        return {prop.strip() for prop in (item.get_properties() or []) if prop}
    except Exception:
        return set()


def _is_navigation(item) -> bool:
    if isinstance(item, epub.EpubNav):
        return True
    if item.get_type() == getattr(ebooklib, "ITEM_NAVIGATION", -1):
        return True
    return "nav" in _properties(item)


def _chapter_items(book: epub.EpubBook) -> list:
    items = []
    seen: set[str] = set()
    for entry in book.spine:
        idref = entry[0] if entry else None
        linear = entry[1] if len(entry) > 1 else "yes"
        if not idref or linear == "no":
            continue
        item = book.get_item_with_id(idref)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if _is_navigation(item):
            continue
        name = item.get_name()
        if name in seen:
            continue
        seen.add(name)
        items.append(item)
    if items:
        return items
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if not _is_navigation(item):
            items.append(item)
    return items


def _item_bytes(item) -> bytes:
    try:
        return item.get_content() or b""
    except Exception:
        return b""


def _chapter_title(item, toc_titles: dict[str, str], index: int) -> str:
    name = posixpath.normpath(item.get_name() or "")
    title = toc_titles.get(name) or toc_titles.get(posixpath.basename(name).lower())
    if title:
        return title
    heading = _first_heading(_item_bytes(item))
    if heading:
        return heading
    return f"Chapitre {index + 1}"


def _html_tree(content: bytes):
    if not content:
        return None
    try:
        return lxml.html.document_fromstring(content)
    except Exception:
        return None


def _signal_key(name: str) -> str:
    return posixpath.basename(posixpath.normpath(name or "")).lower()


def _type_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.split(r"\s+", value or "") if token}


def _structural_signals(book: epub.EpubBook) -> dict[str, set[str]]:
    """Types structurels fournis par le guide EPUB 2 et les landmarks EPUB 3."""
    table: dict[str, set[str]] = {}
    for source in (_guide_signals(book), _landmark_signals(book)):
        for key, tokens in source.items():
            table.setdefault(key, set()).update(tokens)
    return table


def _guide_signals(book: epub.EpubBook) -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for entry in getattr(book, "guide", None) or []:
        if isinstance(entry, dict):
            raw_type = str(entry.get("type") or "")
            href = str(entry.get("href") or "")
        else:
            raw_type = str(entry[0] or "") if len(entry) > 0 else ""
            href = str(entry[2] or "") if len(entry) > 2 else ""
        path, _fragment = _normalise_href(href)
        tokens = _type_tokens(raw_type)
        if path and tokens:
            table.setdefault(_signal_key(path), set()).update(tokens)
    return table


def _landmark_signals(book: epub.EpubBook) -> dict[str, set[str]]:
    table: dict[str, set[str]] = {}
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        content = _item_bytes(item)
        if b"landmarks" not in content:
            continue
        tree = _html_tree(content)
        if tree is None:
            continue
        base = posixpath.dirname(posixpath.normpath(item.get_name() or ""))
        for nav in tree.iter("nav"):
            if "landmarks" not in _type_tokens(nav.get("epub:type") or ""):
                continue
            for anchor in nav.iter("a"):
                tokens = _type_tokens(anchor.get("epub:type") or "")
                path, _fragment = _normalise_href(anchor.get("href") or "")
                if not tokens or not path:
                    continue
                target = posixpath.normpath(posixpath.join(base, path))
                table.setdefault(_signal_key(target), set()).update(tokens)
    return table


def _content_types(content: bytes) -> set[str]:
    """Types portés par les éléments de niveau document du fichier.

    Les ignorer sur les liens et les éléments en ligne évite qu'un simple renvoi
    interne (`<a epub:type="cover">`) fasse classer — et écarter — le chapitre entier.
    """
    tree = _html_tree(content)
    if tree is None:
        return set()
    tokens: set[str] = set()
    for element in tree.iter():
        if not isinstance(element.tag, str) or element.tag.lower() not in _DOCUMENT_LEVEL_TAGS:
            continue
        value = element.get("epub:type") or element.get(_EPUB_TYPE_ATTR) or ""
        if not value:
            continue
        if any(ancestor.tag == "a" for ancestor in element.iterancestors()):
            continue
        tokens.update(_type_tokens(value))
    return tokens


def _classify(name: str, title: str, signals: set[str]) -> str:
    """Range un document dans une zone éditoriale (couverture, corps, notes…)."""
    for kind, tokens in _KIND_TOKENS:
        if signals & tokens:
            return kind
    stem = posixpath.splitext(_signal_key(name))[0]
    label = " ".join((title or "").split()).lower()
    if stem in {"cover", "couverture"} or label in {"couverture", "cover"}:
        return "cover"
    if (
        any(token in stem for token in ("titlepage", "title-page", "contenu-titre"))
        or label in {"page de titre", "page titre", "title page"}
    ):
        return "titlepage"
    if any(token in stem for token in ("copyright", "mentions")) or label in {
        "copyright",
        "mentions légales",
    }:
        return "copyright"
    if stem in {"nav", "toc", "sommaire"} or label in {
        "sommaire",
        "table",
        "table des matières",
        "table of contents",
        "contents",
    }:
        return "toc"
    if stem.startswith(("notes", "appcrit", "endnotes", "footnotes")) or label in {
        "notes",
        "notes de fin",
        "endnotes",
        "footnotes",
    }:
        return "notes"
    if stem.startswith("index") or label == "index":
        return "index"
    return "body"


def _first_heading(content: bytes) -> str:
    tree = _html_tree(content)
    if tree is None:
        return ""
    for tag in _HEADING_TAGS:
        found = tree.find(".//" + tag)
        if found is None:
            continue
        text = " ".join(found.text_content().split())
        if text:
            return text[:200]
    return ""


# --------------------------------------------------------------------------- #
# Table des matières
# --------------------------------------------------------------------------- #


def _normalise_href(href: str) -> tuple[str, str]:
    href = (href or "").strip()
    if not href:
        return "", ""
    href = unquote(href)
    path_part, _, fragment = href.partition("#")
    path_part = path_part.split("?")[0]
    path_part = path_part.lstrip("/")
    if not path_part:
        return "", fragment
    path_part = posixpath.normpath(path_part)
    if path_part in (".", "/"):
        return "", fragment
    return path_part, fragment


def _walk_toc(entries) -> list[TocLink]:
    nodes: list[TocLink] = []
    for entry in entries or []:
        if isinstance(entry, (tuple, list)):
            if len(entry) == 2 and hasattr(entry[0], "title"):
                section, children = entry
                target, fragment = _normalise_href(getattr(section, "href", "") or "")
                child_nodes = _walk_toc(children)
                title = str(getattr(section, "title", "") or "").strip()
                if target:
                    nodes.append(TocLink(title, target, fragment, child_nodes))
                elif child_nodes:
                    # Partie sans lien propre : conserve le titre comme regroupement.
                    nodes.append(TocLink(title, "", "", child_nodes))
                else:
                    nodes.extend(child_nodes)
                continue
            for item in entry:
                nodes.extend(_walk_toc([item]))
            continue
        target, fragment = _normalise_href(getattr(entry, "href", "") or "")
        title = str(getattr(entry, "title", "") or "").strip()
        if not target and not title:
            continue
        nodes.append(TocLink(title, target, fragment))
    return nodes


def _toc_title_index(nodes: list[TocLink]) -> dict[str, str]:
    table: dict[str, str] = {}
    for node in nodes:
        if node.target and node.title:
            table.setdefault(posixpath.normpath(node.target), node.title)
            table.setdefault(posixpath.basename(node.target).lower(), node.title)
        for key, value in _toc_title_index(node.children).items():
            table.setdefault(key, value)
    return table


# --------------------------------------------------------------------------- #
# Couverture, mise en page, avertissements
# --------------------------------------------------------------------------- #


def _cover(book: epub.EpubBook) -> tuple[bytes | None, str | None]:
    for item in book.get_items():
        if "cover-image" in _properties(item) and item.get_type() == ebooklib.ITEM_IMAGE:
            content = _item_bytes(item)
            if content:
                return content, _media_type(item)

    cover_id = ""
    try:
        for value, _attributes in book.get_metadata("OPF", "cover"):
            if isinstance(value, str) and value.strip():
                cover_id = value.strip()
    except Exception:
        cover_id = ""
    if cover_id:
        item = book.get_item_with_id(cover_id)
        if item is not None and item.get_type() == ebooklib.ITEM_IMAGE:
            content = _item_bytes(item)
            if content:
                return content, _media_type(item)

    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        media_type = _media_type(item).lower()
        if media_type.startswith("image/"):
            content = _item_bytes(item)
            if content:
                return content, media_type

    candidates = [
        item
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE)
        if "cover" in (item.get_name() or "").lower()
    ]
    if candidates:
        largest = max(candidates, key=lambda item: len(_item_bytes(item)))
        content = _item_bytes(largest)
        if content:
            return content, _media_type(largest)
    return None, None


def _is_fixed_layout(book: epub.EpubBook) -> bool:
    for value, _attributes in book.get_metadata("OPF", "rendition:layout") or []:
        if isinstance(value, str) and "pre-paginated" in value:
            return True
    for value, attributes in book.get_metadata("OPF", "meta") or []:
        if not isinstance(value, str):
            continue
        if value.strip() == "pre-paginated" and (attributes or {}).get("property") == "rendition:layout":
            return True
    return False


def _warnings(
    title: str,
    author: str,
    cover: bytes | None,
    toc: list[TocLink],
    fixed_layout: bool,
    obfuscated_fonts: bool,
) -> list[str]:
    messages: list[str] = []
    if obfuscated_fonts:
        messages.append(
            "Polices intégrées chiffrées : elles seront remplacées par une police système."
        )
    if fixed_layout:
        messages.append(
            "Mise en page fixe détectée : le PDF peut s'écarter de la maquette d'origine."
        )
    if not toc:
        messages.append("Aucun sommaire dans l'EPUB : les titres de chapitres seront utilisés.")
    if cover is None:
        messages.append("Aucune image de couverture : la page de titre ouvrira le PDF.")
    if not author:
        messages.append("Auteur non renseigné dans les métadonnées.")
    if not title:
        messages.append("Titre non renseigné dans les métadonnées.")
    return messages


# --------------------------------------------------------------------------- #
# Pré-vol : contrôles techniques avant conversion
# --------------------------------------------------------------------------- #


def preflight(document: EpubDocument) -> dict:
    """Mesure le livre et repère ce qui posera problème au rendu papier."""
    items = list(document.book.get_items())
    item_names = {posixpath.normpath(item.get_name() or "") for item in items}

    trees: dict[str, object] = {}
    ids_by_name: dict[str, set[str]] = {}
    for chapter in document.chapters:
        name = posixpath.normpath(chapter.name)
        tree = _html_tree(chapter.content)
        trees[name] = tree
        ids_by_name[name] = _element_ids(tree)

    words = 0
    text_documents = 0
    empty_documents = 0
    images = 0
    missing_images = 0
    internal_links = 0
    broken_links = 0
    css_chunks: list[str] = []

    for chapter in document.chapters:
        name = posixpath.normpath(chapter.name)
        tree = trees.get(name)
        if tree is None:
            empty_documents += 1
            continue
        body = tree.find("body")
        root = body if body is not None else tree
        text = " ".join(root.text_content().split())
        count = len(text.split()) if text else 0
        words += count
        if count >= _MIN_TEXT_WORDS:
            text_documents += 1
        else:
            empty_documents += 1
        for style in tree.iter("style"):
            css_chunks.append(style.text_content() or "")
        for element in tree.iter():
            if not isinstance(element.tag, str):
                continue
            inline = element.get("style")
            if inline:
                css_chunks.append(inline)
            tag = element.tag.lower()
            if tag in ("img", "image"):
                source = _element_source(element)
                if not source:
                    continue
                images += 1
                if not _reference_exists(source, name, item_names):
                    missing_images += 1
            elif tag == "a":
                href = (element.get("href") or "").strip()
                if not href or href.startswith(_EXTERNAL_PREFIXES):
                    continue
                internal_links += 1
                if not _link_resolves(href, name, ids_by_name):
                    broken_links += 1

    style_items = [item for item in items if item.get_type() == ebooklib.ITEM_STYLE]
    for item in style_items:
        try:
            css_chunks.append(item.get_content().decode("utf-8", "replace"))
        except Exception:
            continue
    raw_css = "\n".join(css_chunks)
    css_before = scan_css(raw_css)
    css_after = scan_css(sanitize_css(raw_css))
    css_stats = {name: max(0, css_before[name] - css_after[name]) for name in css_before}
    section_breaks = css_after["page_breaks"]

    fonts = [item for item in items if (item.get_name() or "").lower().endswith(_FONT_SUFFIXES)]
    toc_entries, broken_toc = _toc_stats(document.toc, ids_by_name)

    checks: list[dict[str, str]] = []
    if words:
        checks.append(
            _check(
                "info",
                f"{_number(words)} mots · {_quantity(text_documents, 'document', 'documents')} avec du texte",
            )
        )
    else:
        checks.append(_check("warn", "Aucun texte détecté dans les documents du livre"))
    if empty_documents == 1:
        checks.append(
            _check("info", "1 document sans texte ou très court (couverture, mentions légales…)")
        )
    elif empty_documents:
        checks.append(
            _check(
                "info",
                f"{_number(empty_documents)} documents sans texte ou très courts (couverture, mentions légales…)",
            )
        )
    if toc_entries:
        detail = f"Sommaire : {_quantity(toc_entries, 'entrée', 'entrées')}"
        if broken_toc:
            detail += f", dont {broken_toc} sans cible"
        checks.append(_check("warn" if broken_toc else "ok", detail))
    else:
        checks.append(
            _check("warn", "Aucun sommaire : les titres de chapitres serviront de repères")
        )
    if images:
        detail = _quantity(images, "image")
        if missing_images:
            detail += f", {missing_images} introuvable{'s' if missing_images > 1 else ''}"
        checks.append(_check("warn" if missing_images else "ok", detail))
    if internal_links:
        detail = _quantity(internal_links, "lien interne", "liens internes")
        if broken_links:
            detail += f", {broken_links} sans cible"
        checks.append(_check("warn" if broken_links else "ok", detail))
    if fonts:
        if document.obfuscated_fonts:
            checks.append(
                _check(
                    "warn",
                    f"{_quantity(len(fonts), 'police embarquée', 'polices embarquées')} chiffrée(s) : remplacées par une police système",
                )
            )
        else:
            checks.append(_check("ok", _quantity(len(fonts), "police embarquée", "polices embarquées")))

    neutralised: list[str] = []
    for key, singular, plural in (
        ("page_breaks", "règle de saut de page", "règles de saut de page"),
        ("page_rules", "règle @page", "règles @page"),
        ("fixed_blocks", "bloc positionné", "blocs positionnés"),
        ("clipped_blocks", "contenu rogné", "contenus rognés"),
        ("column_layouts", "mise en colonnes", "mises en colonnes"),
    ):
        if css_stats.get(key):
            neutralised.append(_quantity(css_stats[key], singular, plural))
    if neutralised:
        checks.append(
            _check("info", "Styles d'écran neutralisés : " + ", ".join(neutralised))
        )
    if section_breaks:
        verb = "conservées" if section_breaks > 1 else "conservée"
        checks.append(
            _check(
                "ok",
                f"{_quantity(section_breaks, 'règle de saut de page', 'règles de saut de page')} {verb} pour les sections (appareil critique, annexes)",
            )
        )

    return {
        "epub_version": str(getattr(document.book, "version", "") or ""),
        "words": words,
        "documents": len(document.chapters),
        "text_documents": text_documents,
        "empty_documents": empty_documents,
        "images": images,
        "missing_images": missing_images,
        "embedded_fonts": len(fonts),
        "obfuscated_fonts": document.obfuscated_fonts,
        "stylesheets": len(style_items),
        "toc_entries": toc_entries,
        "broken_toc": broken_toc,
        "internal_links": internal_links,
        "broken_links": broken_links,
        "css": css_stats,
        "section_breaks": section_breaks,
        "checks": checks,
    }


def _check(state: str, label: str) -> dict[str, str]:
    return {"state": state, "label": label}


def _number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _quantity(value: int, singular: str, plural: str | None = None) -> str:
    """Accorde un nom avec son nombre : « 1 image », « 2 images »."""
    word = singular if value == 1 else (plural or f"{singular}s")
    return f"{_number(value)} {word}"


def _element_ids(tree) -> set[str]:
    if tree is None:
        return set()
    ids: set[str] = set()
    for element in tree.iter():
        if not isinstance(element.tag, str):
            continue
        for attribute in ("id", "name"):
            value = element.get(attribute)
            if value:
                ids.add(unquote(value))
    return ids


def _element_source(element) -> str:
    for attribute in ("src", "href", _XLINK_HREF):
        value = element.get(attribute)
        if value:
            return value.strip()
    return ""


def _reference_exists(source: str, base_name: str, item_names: set[str]) -> bool:
    value = (source or "").strip()
    if not value or value.startswith(_EXTERNAL_PREFIXES):
        return True
    path = unquote(value.split("#")[0].split("?")[0]).strip()
    if not path:
        return True
    joined = posixpath.normpath(
        posixpath.join(posixpath.dirname(base_name), path.lstrip("/"))
    )
    return joined in item_names or posixpath.normpath(path.lstrip("/")) in item_names


def _link_resolves(href: str, base_name: str, ids_by_name: dict[str, set[str]]) -> bool:
    path, _, fragment = href.partition("#")
    path = unquote(path).split("?")[0].strip()
    if path:
        joined = posixpath.normpath(
            posixpath.join(posixpath.dirname(base_name), path.lstrip("/"))
        )
        target = joined if joined in ids_by_name else posixpath.normpath(path.lstrip("/"))
    else:
        target = base_name
    ids = ids_by_name.get(target)
    if ids is None:
        return False
    return not fragment or unquote(fragment) in ids


def _toc_stats(nodes: list[TocLink], ids_by_name: dict[str, set[str]]) -> tuple[int, int]:
    entries = 0
    broken = 0
    stack = list(nodes)
    while stack:
        node = stack.pop()
        entries += 1
        stack.extend(node.children)
        if not node.target:
            continue
        target = posixpath.normpath(node.target)
        if target not in ids_by_name:
            broken += 1
        elif node.fragment and unquote(node.fragment) not in ids_by_name[target]:
            broken += 1
    return entries, broken


# --------------------------------------------------------------------------- #
# Système de fichiers
# --------------------------------------------------------------------------- #


def _safe_relative(name: str) -> Path | None:
    name = unquote(name).replace("\\", "/")
    pure = PurePosixPath(name)
    if not pure.parts or pure.is_absolute() or ".." in pure.parts:
        return None
    return Path(*pure.parts)
