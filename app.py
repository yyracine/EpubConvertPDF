"""Reliure — application locale de conversion EPUB vers PDF."""

from __future__ import annotations

import base64
import io
import os
import re
import tempfile
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from converter import epub_reader, quality, renderer
from converter.assembler import Options, build_document
from converter.errors import ConversionError
from converter.epub_reader import MAX_EPUB_BYTES
from converter.jobs import Job, JobStore

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = MAX_EPUB_BYTES

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + 4 * 1024 * 1024,
    JSON_SORT_KEYS=False,
)

JOB_STORE = JobStore(OUTPUT_DIR / "jobs")


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    status = renderer.dependency_status()
    return jsonify(status)


# --------------------------------------------------------------------------- #
# Analyse d'un EPUB
# --------------------------------------------------------------------------- #


@app.post("/api/inspect")
def inspect_endpoint():
    try:
        with tempfile.TemporaryDirectory(prefix="reliure-", dir=OUTPUT_DIR) as work:
            path, original = _receive_epub(Path(work) / "source.epub")
            document = epub_reader.read_book(path, source_name=original)
            summary = document.summary()
            summary["cover"] = _cover_thumbnail(document)
            return jsonify(
                {
                    "file": {"name": original, "size": path.stat().st_size},
                    "book": summary,
                    "preflight": epub_reader.preflight(document),
                    "suggested_filename": _suggested_filename(document.title, original),
                }
            )
    except ConversionError as exc:
        return _error(exc.message, hint=exc.hint, status=exc.status)
    except Exception:
        return _error(
            "L'analyse du fichier a échoué.",
            hint="Vérifiez que l'EPUB s'ouvre correctement dans une liseuse, puis réessayez.",
            status=500,
        )


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #


@app.post("/api/convert")
def convert_endpoint():
    job = JOB_STORE.create()
    try:
        path, original = _receive_epub(job.directory / "source.epub")
    except ConversionError as exc:
        JOB_STORE.delete(job.id)
        return _error(exc.message, hint=exc.hint, status=exc.status)

    options = Options(
        include_toc=_flag("toc", True),
        include_page_numbers=_flag("page_numbers", True),
        include_cover_page=_flag("cover_page", True),
        page_format=(request.form.get("page_format") or "a5").strip().lower(),
        margins=(request.form.get("margins") or "normal").strip().lower(),
        font_size=_decimal("font_size", Options.font_size),
        line_height=_decimal("line_height", Options.line_height),
    )
    job.set(
        filename=_pdf_filename(request.form.get("filename"), original),
        status="running",
        stage="reading",
        progress=8,
        message="Analyse de l'EPUB",
    )
    thread = threading.Thread(
        target=_run_conversion,
        args=(job, path, original, options),
        daemon=True,
    )
    thread.start()
    return jsonify(job.snapshot()), 202


def _run_conversion(job: Job, path: Path, original: str, options: Options) -> None:
    try:
        document = epub_reader.read_book(path, source_name=original)

        job.set(stage="extracting", progress=32, message="Extraction du contenu")
        media = job.directory / "media"
        epub_reader.extract_resources(document, media)

        job.set(stage="assembling", progress=55, message="Composition de la mise en page")
        document_html = build_document(document, media, options)

        job.set(stage="rendering", progress=78, message="Rendu du PDF")
        pdf_bytes = renderer.render_pdf(document_html, base_url=media)

        job.set(stage="finalizing", progress=94, message="Contrôle qualité du PDF")
        target = job.directory / job.filename
        target.write_bytes(pdf_bytes)

        report = quality.inspect(
            pdf_bytes,
            document=document,
            expect_bookmarks=options.include_toc,
        )
        pages = report.get("pages") or renderer.count_pages(pdf_bytes)
        job.set(
            status="done",
            stage="done",
            progress=100,
            message="Conversion terminée",
            pages=pages or None,
            size=len(pdf_bytes),
            report=report,
        )
    except ConversionError as exc:
        job.set(
            status="error",
            stage="error",
            message=exc.message,
            error=exc.message,
            hint=exc.hint,
        )
    except Exception as exc:  # dernier filet : ne jamais laisser un job bloqué
        job.set(
            status="error",
            stage="error",
            message="Une erreur inattendue est survenue pendant la conversion.",
            error="La conversion a échoué.",
            hint=f"Détail technique : {type(exc).__name__}.",
        )


@app.get("/api/jobs/<job_id>")
def job_status(job_id: str):
    job = JOB_STORE.get(job_id)
    if job is None:
        return _error("Conversion introuvable ou expirée.", status=404)
    return jsonify(job.snapshot())


@app.get("/api/jobs/<job_id>/download")
def job_download(job_id: str):
    job = JOB_STORE.get(job_id)
    if job is None:
        return _error("Conversion introuvable ou expirée.", status=404)
    snapshot = job.snapshot()
    if snapshot["status"] != "done":
        return _error("Le PDF n'est pas encore disponible.", status=409)
    pdf = job.directory / snapshot["filename"]
    if not pdf.is_file():
        return _error("Le fichier PDF a expiré.", status=404)
    return send_file(
        pdf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=snapshot["filename"],
        max_age=0,
    )


@app.delete("/api/jobs/<job_id>")
def job_delete(job_id: str):
    if not JOB_STORE.delete(job_id):
        return jsonify({"deleted": False}), 404
    return jsonify({"deleted": True})


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #


def _receive_epub(destination: Path) -> tuple[Path, str]:
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ConversionError(
            "Aucun fichier reçu.",
            hint="Choisissez un fichier EPUB, puis relancez la conversion.",
        )
    original = Path(upload.filename.replace("\\", "/")).name
    if not original.lower().endswith(".epub"):
        raise ConversionError(
            "Seuls les fichiers .epub sont acceptés.",
            hint="Sélectionnez un fichier dont l'extension est .epub.",
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    upload.save(destination)

    size = destination.stat().st_size
    if size == 0:
        raise ConversionError("Le fichier est vide.", hint="Choisissez un autre EPUB.")
    if size > MAX_UPLOAD_BYTES:
        raise ConversionError(
            f"Le fichier dépasse la limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo.",
            status=413,
        )
    return destination, original


def _flag(name: str, default: bool = True) -> bool:
    raw = request.form.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "on", "yes"}


def _decimal(name: str, default: float) -> float:
    try:
        return float(request.form.get(name, ""))
    except (TypeError, ValueError):
        return default


def _suggested_filename(title: str, original: str) -> str:
    stem = _clean_stem(title) or _clean_stem(Path(original).stem) or "document"
    return f"{stem}.pdf"


def _pdf_filename(raw: str | None, original: str) -> str:
    stem = _clean_stem(raw or "")
    if not stem:
        stem = _clean_stem(Path(original).stem) or "document"
    return f"{stem[:120]}.pdf"


def _clean_stem(value: str) -> str:
    stem = re.sub(r"(?i)\.pdf$", "", (value or "").strip())
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    return stem


def _cover_thumbnail(document: epub_reader.EpubDocument) -> str | None:
    raw = document.cover_bytes
    if not raw:
        return None
    media_type = (document.cover_media_type or "").lower()

    if media_type == "image/svg+xml":
        if len(raw) > 400_000:
            return None
        return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")

    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            thumb = image.copy()
            thumb.thumbnail((280, 420))
            buffer = io.BytesIO()
            if thumb.mode in ("RGBA", "LA", "P"):
                thumb.convert("RGBA").save(buffer, "PNG", optimize=True)
                mime = "image/png"
            else:
                thumb.convert("RGB").save(buffer, "JPEG", quality=86, optimize=True)
                mime = "image/jpeg"
        return f"data:{mime};base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


def _error(message: str, *, hint: str | None = None, status: int = 400):
    payload = {"error": message}
    if hint:
        payload["hint"] = hint
    return jsonify(payload), status


@app.errorhandler(RequestEntityTooLarge)
def _too_large(_exception):
    return _error(
        f"Le fichier dépasse la limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} Mo.",
        status=413,
    )


if __name__ == "__main__":
    port = int(os.environ.get("RELIURE_PORT", "8765"))
    if os.environ.get("RELIURE_OPEN_BROWSER", "1") != "0":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    print(f"Reliure — conversion EPUB vers PDF")
    print(f"Interface : http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
