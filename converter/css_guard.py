"""Neutralisation du CSS des EPUB avant la composition papier.

Les feuilles de style d'un EPUB visent l'écran : sauts de page à chaque paragraphe,
blocs en position absolue, colonnes multiples, tailles en unités de fenêtre. Sur
papier, ces règles creusent des pages blanches ou rognent le texte. Ce module retire
uniquement les déclarations dangereuses, ramène les tailles de police absolues à
l'échelle du corps choisi et conserve le reste du style de l'EPUB.
"""

from __future__ import annotations

import re

_VENDOR_PREFIX = re.compile(r"^-(?:webkit|moz|ms|o)-", re.IGNORECASE)
_VIEWPORT_UNIT = re.compile(r"\d+(?:\.\d+)?\s*(?:vh|vw|vmin|vmax|dvh|dvw)\b", re.IGNORECASE)
_TRAILING_IMPORTANT = re.compile(r"\s*!\s*important\s*$", re.IGNORECASE)
_INDENT_VALUE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(px|pt|pc|mm|cm|in|em|rem)$", re.IGNORECASE)

# Métriques du texte : le corps et l'interligne choisis dans Reliure doivent primer.
# Seules les valeurs relatives (qui suivent la taille de base) sont conservées.
_RELATIVE_TEXT_METRIC = re.compile(
    r"^(?:0|0\.0+|[+-]?\d+(?:\.\d+)?\s*(?:%|em|rem|ex|ch)|larger|smaller|inherit|unset|revert(?:-layer)?)$",
    re.IGNORECASE,
)

# Corps par défaut de Reliure : les tailles absolues de l'EPUB y sont rapportées, si
# bien qu'au corps par défaut le rendu reste celui voulu par le livre.
DEFAULT_FONT_PT = 11.0

_ABSOLUTE_SIZE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(pt|px|pc|mm|cm|in|q)$", re.IGNORECASE)
_PT_PER_UNIT = {
    "pt": 1.0,
    "px": 0.75,
    "pc": 12.0,
    "mm": 72 / 25.4,
    "cm": 72 / 2.54,
    "in": 72.0,
    "q": 72 / 101.6,
}
# Mots-clés absolus de CSS Fonts 4, en multiples du corps.
_ABSOLUTE_SIZE_KEYWORDS = {
    "xx-small": 0.579,
    "x-small": 0.694,
    "small": 0.833,
    "medium": 1.0,
    "large": 1.2,
    "x-large": 1.44,
    "xx-large": 1.728,
    "xxx-large": 2.074,
}
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Sauts de page imposés par la mise en page d'origine : seuls les chapitres de
# Reliure doivent ouvrir une page.
_PAGE_BREAK_PROPERTIES = frozenset(
    {"break-before", "break-after", "page-break-before", "page-break-after"}
)

# Propriétés dimensionnées : une taille en unité de fenêtre n'a aucun sens sur
# une page A5 et provoque des débordements.
_VIEWPORT_SIZED_PROPERTIES = frozenset(
    {
        "width",
        "min-width",
        "max-width",
        "height",
        "min-height",
        "max-height",
        "font-size",
        "line-height",
        "top",
        "right",
        "bottom",
        "left",
        "margin",
        "margin-top",
        "margin-right",
        "margin-bottom",
        "margin-left",
        "padding",
        "padding-top",
        "padding-right",
        "padding-bottom",
        "padding-left",
        "flex-basis",
    }
)

_POSITIONING_VALUES = ("fixed", "absolute", "sticky", "running(")
_HIDDEN_OVERFLOW = frozenset({"hidden", "clip", "scroll"})
_INDENT_THRESHOLD_PX = 200.0
_PX_PER_UNIT = {
    "px": 1.0,
    "pt": 4 / 3,
    "pc": 16.0,
    "mm": 3.7795,
    "cm": 37.795,
    "in": 96.0,
    "em": 16.0,
    "rem": 16.0,
}

# At-rules dont le contenu est un groupe de règles (et non des déclarations).
_CONDITIONAL_AT_RULES = ("@media", "@supports", "@layer", "@container", "@document", "@scope")

_SELECTOR_NAME = re.compile(r"[.#]([A-Za-z_][\w-]*)")
# Sections dont l'ouverture mérite une page neuve, même dans un EPUB qui multiplie les
# sauts de page. Les pages blanches et les blocs de couverture restent exclus.
_SECTION_TOKENS = (
    "appcrit",
    "apparatus",
    "annex",
    "appendi",
    "notes",
    "notices",
    "bibliograph",
    "glossair",
    "glossary",
    "index",
    "supplement",
    "postface",
    "epilogue",
)

_SCAN_PATTERNS = {
    "page_breaks": re.compile(
        r"(?:break-before|break-after|page-break-before|page-break-after)"
        r"\s*:\s*(?:page|always|left|right|recto|verso|all)\b",
        re.IGNORECASE,
    ),
    "fixed_blocks": re.compile(r"position\s*:\s*(?:fixed|absolute|sticky)\b", re.IGNORECASE),
    "clipped_blocks": re.compile(
        r"overflow(?:-[xy])?\s*:\s*(?:hidden|clip|scroll)\b", re.IGNORECASE
    ),
    "column_layouts": re.compile(r"(?:column-count|column-width|columns)\s*:", re.IGNORECASE),
    "page_rules": re.compile(r"@page\b", re.IGNORECASE),
    "forced_important": re.compile(r"!\s*important\b", re.IGNORECASE),
}


def scan_css(text: str) -> dict[str, int]:
    """Compte les règles d'écran qui devront être neutralisées à la composition."""
    source = text or ""
    return {name: len(pattern.findall(source)) for name, pattern in _SCAN_PATTERNS.items()}


def sanitize_css(css: str) -> str:
    """Retire d'une feuille de style les règles incompatibles avec une page imprimée."""
    if not css or not css.strip():
        return ""
    return _sanitize_blocks(_COMMENT.sub("", css)).strip()


def sanitize_declarations(block: str, selector: str = "") -> str:
    """Nettoie une liste de déclarations (attribut `style` ou contenu d'une règle)."""
    if not block:
        return ""
    kept: list[str] = []
    for raw in _split_top_level(block, ";"):
        piece = raw.strip()
        if not piece:
            continue
        prop, separator, value = _split_property(piece)
        if not separator:
            kept.append(piece)
            continue
        cleaned = _clean_declaration(prop.strip(), value.strip(), selector)
        if cleaned:
            kept.append(cleaned)
    return "; ".join(kept)


# --------------------------------------------------------------------------- #
# Déclarations
# --------------------------------------------------------------------------- #


def _scaled_font_size(value: str) -> str | None:
    """Ramène une taille absolue de l'EPUB à l'échelle du corps choisi, en `rem`.

    Les tailles sont rapportées au corps par défaut (11 pt) puis exprimées en `rem` :
    à ce corps le rendu reste celui de l'EPUB, et pour un autre corps toute
    la hiérarchie grandit dans la même proportion, si bien que notes et légendes restent
    proportionnellement plus petites. `rem` évite en outre qu'une taille absolue
    imbriquée se multiplie en cascade.
    """
    text = (value or "").strip().lower()
    keyword = _ABSOLUTE_SIZE_KEYWORDS.get(text)
    if keyword is not None:
        return _rem(keyword)
    match = _ABSOLUTE_SIZE.match(text)
    if match is None:
        return None
    points = float(match.group(1)) * _PT_PER_UNIT[match.group(2).lower()]
    return _rem(points / DEFAULT_FONT_PT)


def _rem(ratio: float) -> str:
    return f"{round(ratio, 4):g}rem"


def _font_shorthand(value: str) -> list[str] | None:
    """Réécrit un raccourci `font` à taille absolue à l'échelle du corps.

    `font: 12pt/1.4 Georgia, serif` fixe corps et interligne : la taille devient un
    `rem` et l'interligne du raccourci disparaît. Le raccourci remettant `line-height`
    à `normal`, un `inherit` final rend l'interligne choisi. Un raccourci à taille
    relative (`1.2em`) suit déjà le corps et reste intact.
    """
    tokens = _split_font_tokens(value)
    for position, token in enumerate(tokens):
        scaled = _scaled_font_size(token)
        if scaled is not None:
            break
    else:
        return None
    family = tokens[position + 1 :]
    if family[:1] == ["/"]:
        family = family[2:]  # interligne figé du raccourci
    if not family:
        # Sans famille il ne reste plus de raccourci valide : la taille seule suffit.
        return [f"font-size: {scaled}"]
    return [f"font: {' '.join(tokens[:position] + [scaled] + family)}", "line-height: inherit"]


def _split_font_tokens(value: str) -> list[str]:
    """Découpe un raccourci `font` en jetons, en isolant les barres obliques."""
    tokens: list[str] = []
    current: list[str] = []
    depth = 0
    quote = ""
    for char in value or "":
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (char == "/" or char.isspace()):
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
            if char == "/":
                tokens.append("/")
            continue
        current.append(char)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens


def _clean_declaration(prop: str, value: str, selector: str = "") -> str | None:
    if not prop or not value:
        return None

    name = _VENDOR_PREFIX.sub("", prop).lower()
    important = ""
    match = _TRAILING_IMPORTANT.search(value)
    if match:
        important = " !important"
        value = value[: match.start()].strip()
    lowered = value.lower()

    if name == "font-size":
        # Taille absolue de l'EPUB : on la ramène à l'échelle du corps choisi plutôt que
        # de la supprimer, pour que la hiérarchie du livre suive le réglage.
        scaled = _scaled_font_size(value)
        if scaled is not None:
            return f"font-size: {scaled}{important}"
        if not _RELATIVE_TEXT_METRIC.match(value):
            return None
    elif name == "line-height" and not _RELATIVE_TEXT_METRIC.match(value):
        # Interligne figé de l'EPUB : l'interligne choisi dans Reliure doit s'appliquer.
        return None
    elif name == "font":
        # Raccourci `font` : il figerait le corps et l'interligne avec le reste de la police.
        declarations = _font_shorthand(value)
        if declarations is not None:
            return "; ".join(f"{declaration}{important}" for declaration in declarations)
    if name in _PAGE_BREAK_PROPERTIES:
        # Une section nommée (appareil critique, annexe) garde son ouverture sur une
        # page neuve ; les sauts de paragraphe et les pages blanches disparaissent.
        if _is_section_opener(selector):
            return f"{prop}: {value}{important}"
        return None
    if name == "page":
        # Page nommée de l'EPUB : elle écraserait nos pages couverture/sommaire.
        return None
    if name == "columns" or name.startswith("column"):
        return None
    if name == "position" and lowered.startswith(_POSITIONING_VALUES):
        return None
    if name in ("overflow", "overflow-x", "overflow-y") and _HIDDEN_OVERFLOW.intersection(
        lowered.split()
    ):
        return f"{prop}: visible{important}"
    if name in _VIEWPORT_SIZED_PROPERTIES and _VIEWPORT_UNIT.search(value):
        return None
    if name == "text-indent" and _is_offscreen_indent(value):
        return None
    if name == "clip" and lowered != "auto":
        return None
    return f"{prop}: {value}{important}"


def _is_section_opener(selector: str) -> bool:
    names = [match.lower() for match in _SELECTOR_NAME.findall(selector or "")]
    return any(token in name for name in names for token in _SECTION_TOKENS)


def _is_offscreen_indent(value: str) -> bool:
    match = _INDENT_VALUE.match(value.strip())
    if match is None:
        return False
    amount = float(match.group(1))
    if amount >= 0:
        return False
    pixels = abs(amount) * _PX_PER_UNIT[match.group(2).lower()]
    return pixels >= _INDENT_THRESHOLD_PX


# --------------------------------------------------------------------------- #
# Analyse du texte CSS
# --------------------------------------------------------------------------- #


def _sanitize_blocks(css: str) -> str:
    pieces: list[str] = []
    index = 0
    while index < len(css):
        stops = [
            pos
            for pos in (css.find("{", index), css.find(";", index), css.find("}", index))
            if pos >= 0
        ]
        if not stops:
            pieces.append(css[index:])
            break
        position = min(stops)
        if css[position] != "{":
            # Directive simple (`@charset ...;`) ou accolade orpheline : on conserve.
            pieces.append(css[index : position + 1])
            index = position + 1
            continue
        end = _matching_brace(css, position)
        if end < 0:
            pieces.append(css[index:])
            break
        prelude = css[index : position + 1]
        block = css[position + 1 : end]
        lowered = prelude.strip().lower()
        if lowered.startswith("@page"):
            # Format, marges et folios appartiennent à Reliure : le bloc disparaît.
            pieces.append("")
        elif lowered.startswith(_CONDITIONAL_AT_RULES):
            pieces.append(prelude + _sanitize_blocks(block) + "}")
        elif lowered.startswith("@"):
            # @font-face, @keyframes… : aucune déclaration dangereuse, on conserve.
            pieces.append(prelude + block + "}")
        else:
            pieces.append(prelude + sanitize_declarations(block, prelude[:-1].strip()) + "}")
        index = end + 1
    return "".join(pieces)


def _matching_brace(text: str, start: int) -> int:
    depth = 0
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _split_top_level(text: str, separator: str) -> list[str]:
    """Découpe sur un séparateur de premier niveau, hors parenthèses et chaînes."""
    items: list[str] = []
    depth = 0
    quote = ""
    start = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote and text[index - 1] != "\\":
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0:
            items.append(text[start:index])
            start = index + 1
    items.append(text[start:])
    return items


def _split_property(piece: str) -> tuple[str, str, str]:
    """Sépare `propriété: valeur`, en gardant les deux-points des valeurs (`filter: progid:`)."""
    parts = _split_top_level(piece, ":")
    if len(parts) < 2:
        return piece, "", ""
    return parts[0], ":", ":".join(parts[1:])
