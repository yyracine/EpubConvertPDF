"""Rendu PDF via WeasyPrint et diagnostic des bibliothèques natives."""

from __future__ import annotations

import io
import os
from pathlib import Path

from .errors import ConversionError, DependencyError

_DLL_CANDIDATES = (
    Path(r"C:\msys64\ucrt64\bin"),
    Path(r"C:\msys64\mingw64\bin"),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GTK3-Runtime Win64" / "bin",
)
_PANGO_DLLS = ("libpango-1.0-0.dll", "pango-1.0-0.dll")

_PANGO_HINT = (
    "WeasyPrint a besoin des bibliothèques natives Pango. Sous Windows : "
    "installez MSYS2 (winget install MSYS2.MSYS2), puis dans un terminal MSYS2 UCRT64 exécutez "
    "pacman -S --needed mingw-w64-ucrt-x86_64-pango. "
    "Définissez ensuite la variable d'environnement WEASYPRINT_DLL_DIRECTORIES = C:\\msys64\\ucrt64\\bin "
    "et relancez Reliure."
)


def ensure_native_libraries() -> None:
    """Indique à WeasyPrint où trouver Pango, sans modifier le PATH global."""
    if os.environ.get("WEASYPRINT_DLL_DIRECTORIES"):
        return
    found = [
        str(candidate)
        for candidate in _DLL_CANDIDATES
        if any((candidate / dll).exists() for dll in _PANGO_DLLS)
    ]
    if found:
        os.environ["WEASYPRINT_DLL_DIRECTORIES"] = os.pathsep.join(found)


def dependency_status() -> dict:
    ensure_native_libraries()
    status = {
        "ready": False,
        "pango": False,
        "weasyprint": None,
        "dll_directories": os.environ.get("WEASYPRINT_DLL_DIRECTORIES", ""),
        "error": None,
        "hint": None,
    }

    try:
        import weasyprint
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["hint"] = _PANGO_HINT
        return status

    status["weasyprint"] = getattr(weasyprint, "__version__", "inconnue")
    try:
        from weasyprint.text.ffi import pango  # noqa: F401
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["hint"] = _PANGO_HINT
        return status

    status["pango"] = True
    status["ready"] = True
    return status


def render_pdf(document_html: str, base_url: Path) -> bytes:
    ensure_native_libraries()
    try:
        from weasyprint import HTML
    except Exception as exc:
        raise DependencyError(
            "Le moteur de rendu PDF (WeasyPrint) n'a pas pu démarrer.",
            hint=_PANGO_HINT,
        ) from exc

    try:
        pdf = HTML(string=document_html, base_url=str(base_url) + os.sep).write_pdf()
    except Exception as exc:
        raise ConversionError(
            "La composition du PDF a échoué.",
            hint=f"Détail technique : {type(exc).__name__}: {exc}",
            status=500,
        ) from exc

    if not pdf:
        raise ConversionError("Le PDF généré est vide.", status=500)
    return pdf


def count_pages(pdf_bytes: bytes) -> int:
    try:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        return 0
