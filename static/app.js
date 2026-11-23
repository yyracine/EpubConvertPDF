(() => {
  "use strict";

  const MAX_BYTES = 100 * 1024 * 1024;
  const STAGE_ORDER = ["reading", "extracting", "assembling", "rendering"];
  const LAYOUT_PRESETS = {
    roman: { format: "a5", margins: "normal", size: "11", leading: "1.55" },
    essai: { format: "a5", margins: "wide", size: "11", leading: "1.7" },
    manuel: { format: "a4", margins: "narrow", size: "11", leading: "1.4" },
  };
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const $ = (id) => document.getElementById(id);
  const els = {
    engine: $("engine"),
    engineLabel: document.querySelector("#engine .engine-label"),
    dropzone: $("dropzone"),
    fileInput: $("fileInput"),
    analyzing: $("analyzing"),
    bookCard: $("bookCard"),
    bookCover: $("bookCover"),
    bookCoverImage: $("bookCoverImage"),
    coverFallback: $("coverFallback"),
    bookTitle: $("bookTitle"),
    bookAuthor: $("bookAuthor"),
    bookMeta: $("bookMeta"),
    bookWarnings: $("bookWarnings"),
    bookChecks: $("bookChecks"),
    changeFile: $("changeFile"),
    fileError: $("fileError"),
    filename: $("filename"),
    convertBtn: $("convertBtn"),
    convertSpinner: $("convertSpinner"),
    progress: $("progress"),
    progressLabel: $("progressLabel"),
    progressPercent: $("progressPercent"),
    progressBar: $("progressBar"),
    steps: $("steps"),
    result: $("result"),
    resultTitle: $("resultTitle"),
    resultMeta: $("resultMeta"),
    resultReport: $("resultReport"),
    reportSummary: $("reportSummary"),
    reportChecks: $("reportChecks"),
    downloadBtn: $("downloadBtn"),
    resetBtn: $("resetBtn"),
    errorPanel: $("errorPanel"),
    errorTitle: $("errorTitle"),
    errorBody: $("errorBody"),
    errorDetails: $("errorDetails"),
    errorHint: $("errorHint"),
    optToc: $("optToc"),
    optPages: $("optPages"),
    optCover: $("optCover"),
    optFormat: $("optFormat"),
    optMargins: $("optMargins"),
    optFontSize: $("optFontSize"),
    optLineHeight: $("optLineHeight"),
    layoutNote: $("layoutNote"),
  };

  const state = {
    file: null,
    info: null,
    jobId: null,
    pollTimer: null,
    busy: false,
  };

  const numberFr = new Intl.NumberFormat("fr-FR");

  function formatBytes(bytes) {
    if (!bytes) return "—";
    const units = ["o", "Ko", "Mo", "Go"];
    let value = bytes;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    const digits = index === 0 ? 0 : 1;
    return `${new Intl.NumberFormat("fr-FR", { maximumFractionDigits: digits }).format(value)} ${units[index]}`;
  }

  function plural(count, word) {
    return `${numberFr.format(count)} ${word}${count > 1 ? "s" : ""}`;
  }

  /* --- Moteur ---------------------------------------------------------- */

  function setEngine(mode, label) {
    if (!els.engine) return;
    els.engine.dataset.state = mode;
    if (els.engineLabel) els.engineLabel.textContent = label;
  }

  async function checkEngine() {
    try {
      const response = await fetch("/api/health", { headers: { Accept: "application/json" } });
      const data = await response.json();
      if (data.ready) {
        setEngine("ready", "Moteur prêt");
        return;
      }
      setEngine("error", "Moteur indisponible");
      showError(
        "Le moteur de rendu PDF n'est pas disponible.",
        data.hint || "WeasyPrint n'a pas pu charger les bibliothèques natives Pango."
      );
    } catch (error) {
      setEngine("error", "Serveur injoignable");
    }
  }

  /* --- Messages -------------------------------------------------------- */

  function showError(message, hint) {
    els.errorTitle.textContent = message;
    els.errorBody.classList.add("hidden");
    els.errorBody.textContent = "";
    if (hint) {
      els.errorHint.textContent = hint;
      els.errorDetails.classList.remove("hidden");
      els.errorDetails.open = true;
    } else {
      els.errorDetails.classList.add("hidden");
    }
    els.errorPanel.classList.remove("hidden");
  }

  function clearError() {
    els.errorPanel.classList.add("hidden");
  }

  function setFileError(message) {
    els.fileError.textContent = message || "";
    els.fileError.classList.toggle("hidden", !message);
  }

  /* --- Mise en page --------------------------------------------------- */

  function updateLayoutNote() {
    if (!els.layoutNote) return;
    const format = els.optFormat.selectedOptions[0];
    const margins = els.optMargins.selectedOptions[0];
    els.layoutNote.textContent = [
      format?.dataset.short || "A5",
      `${margins?.dataset.mm || "17"} mm`,
      `${els.optFontSize.value} pt`,
    ].join(" · ");
  }

  function applyPreset(name) {
    const preset = LAYOUT_PRESETS[name];
    if (!preset) return;
    els.optFormat.value = preset.format;
    els.optMargins.value = preset.margins;
    els.optFontSize.value = preset.size;
    els.optLineHeight.value = preset.leading;
    updateLayoutNote();
  }

  /* --- Fichier --------------------------------------------------------- */

  function resetResult() {
    els.result.classList.add("hidden");
    els.resultReport.classList.add("hidden");
    els.downloadBtn.removeAttribute("href");
  }

  function showDropzone() {
    els.dropzone.classList.remove("hidden");
    els.bookCard.classList.add("hidden");
    els.analyzing.classList.add("hidden");
  }

  async function acceptFile(file) {
    clearError();
    resetResult();

    if (!file.name.toLowerCase().endsWith(".epub")) {
      setFileError("Ce fichier n'est pas un EPUB. Choisissez un fichier dont l'extension est .epub.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setFileError(`Fichier trop volumineux (${formatBytes(file.size)}). La limite est de 100 Mo.`);
      return;
    }

    setFileError("");
    await discardJob();
    state.file = file;
    state.info = null;

    els.dropzone.classList.add("hidden");
    els.bookCard.classList.add("hidden");
    els.analyzing.classList.remove("hidden");
    els.convertBtn.disabled = true;

    const form = new FormData();
    form.append("file", file);

    try {
      const response = await fetch("/api/inspect", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw data;
      renderBook(data);
    } catch (error) {
      state.file = null;
      showDropzone();
      showError(error.error || "Impossible de lire cet EPUB.", error.hint);
    } finally {
      els.analyzing.classList.add("hidden");
    }
  }

  function renderBook(data) {
    const book = data.book || {};
    const preflight = data.preflight || {};
    state.info = book;

    els.bookTitle.textContent = book.title || data.file?.name || "Sans titre";
    els.bookAuthor.textContent = book.author || "Auteur inconnu";

    els.bookMeta.innerHTML = "";
    const meta = [
      plural(book.chapters || 0, "chapitre"),
      formatBytes(data.file?.size),
      book.language_label || "Langue non renseignée",
    ];
    meta.forEach((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      els.bookMeta.appendChild(item);
    });

    if (book.cover) {
      els.bookCoverImage.src = book.cover;
      els.bookCoverImage.alt = `Couverture de ${book.title || "l'EPUB"}`;
      els.bookCoverImage.classList.remove("hidden");
      els.coverFallback.classList.add("hidden");
    } else {
      els.bookCoverImage.removeAttribute("src");
      els.bookCoverImage.classList.add("hidden");
      els.coverFallback.classList.remove("hidden");
    }

    const warnings = Array.isArray(book.warnings) ? book.warnings : [];
    els.bookWarnings.innerHTML = "";
    warnings.forEach((text) => {
      const item = document.createElement("li");
      item.textContent = text;
      els.bookWarnings.appendChild(item);
    });
    els.bookWarnings.classList.toggle("hidden", warnings.length === 0);

    const checks = Array.isArray(preflight.checks) ? preflight.checks : [];
    els.bookChecks.innerHTML = "";
    checks.forEach((entry) => {
      const item = document.createElement("li");
      item.dataset.state = entry.state || "info";
      item.textContent = entry.label;
      els.bookChecks.appendChild(item);
    });
    els.bookChecks.classList.toggle("hidden", checks.length === 0);

    els.filename.value = (data.suggested_filename || "").replace(/\.pdf$/i, "");
    els.bookCard.classList.remove("hidden");
    els.dropzone.classList.add("hidden");
    els.convertBtn.disabled = false;
  }

  /* --- Progression ----------------------------------------------------- */

  function setProgress(stage, percent, message) {
    els.progress.classList.remove("hidden");
    els.progressBar.style.width = `${percent}%`;
    els.progressPercent.textContent = `${percent} %`;
    els.progressLabel.textContent = message || "Conversion…";
    paintSteps(stage);
  }

  function paintSteps(stage) {
    const effective = stage === "finalizing" ? "rendering" : stage;
    const current = STAGE_ORDER.indexOf(effective);
    Array.from(els.steps.children).forEach((step) => {
      const index = STAGE_ORDER.indexOf(step.dataset.stage);
      const done = stage === "done" || (current > -1 && index < current);
      step.classList.toggle("is-done", done);
      step.classList.toggle("is-active", !done && index === current);
    });
  }

  async function convert() {
    if (!state.file || state.busy) return;

    state.busy = true;
    clearError();
    resetResult();
    setFileError("");
    els.convertBtn.disabled = true;
    els.convertSpinner.classList.remove("hidden");
    setProgress("reading", 5, "Préparation…");

    const form = new FormData();
    form.append("file", state.file);
    form.append("filename", els.filename.value.trim());
    form.append("toc", els.optToc.checked ? "1" : "0");
    form.append("page_numbers", els.optPages.checked ? "1" : "0");
    form.append("cover_page", els.optCover.checked ? "1" : "0");
    form.append("page_format", els.optFormat.value);
    form.append("margins", els.optMargins.value);
    form.append("font_size", els.optFontSize.value);
    form.append("line_height", els.optLineHeight.value);

    try {
      const response = await fetch("/api/convert", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw data;
      state.jobId = data.id;
      pollJob();
    } catch (error) {
      finishWithError(error.error || "La conversion n'a pas pu démarrer.", error.hint);
    }
  }

  async function pollJob() {
    if (!state.jobId) return;
    try {
      const response = await fetch(`/api/jobs/${state.jobId}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw await response.json().catch(() => ({}));
      const job = await response.json();

      applyJob(job);

      if (job.status === "done") {
        finishWithSuccess(job);
        return;
      }
      if (job.status === "error") {
        finishWithError(job.error || job.message || "La conversion a échoué.", job.hint);
        return;
      }
      state.pollTimer = window.setTimeout(pollJob, 650);
    } catch (error) {
      finishWithError(error.error || "La conversion a été interrompue.", error.hint);
    }
  }

  function applyJob(job) {
    const percent = Math.max(0, Math.min(100, Number(job.progress) || 0));
    els.progress.classList.remove("hidden");
    els.progressBar.style.width = `${percent}%`;
    els.progressPercent.textContent = `${percent} %`;
    els.progressLabel.textContent = job.message || "Conversion…";
    paintSteps(job.stage);
  }

  function finishWithSuccess(job) {
    state.busy = false;
    els.convertSpinner.classList.add("hidden");
    els.convertBtn.disabled = false;
    els.progressBar.style.width = "100%";
    els.progressPercent.textContent = "100 %";
    els.progressLabel.textContent = "Conversion terminée";
    paintSteps("done");

    els.resultTitle.textContent = job.filename || "Votre PDF";
    const parts = [];
    if (job.pages) parts.push(plural(job.pages, "page"));
    if (job.size) parts.push(formatBytes(job.size));
    parts.push(`format ${els.optFormat.selectedOptions[0]?.dataset.short || "A5"}`);
    els.resultMeta.textContent = parts.join(" · ");

    els.downloadBtn.href = `/api/jobs/${job.id}/download`;
    els.downloadBtn.setAttribute("download", job.filename || "document.pdf");
    renderReport(job.report);
    els.result.classList.remove("hidden");
    els.result.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  }

  function renderReport(report) {
    const checks = report && Array.isArray(report.checks) ? report.checks : [];
    const worried = report?.state === "warn";

    els.reportChecks.innerHTML = "";
    checks.forEach((entry) => {
      const item = document.createElement("li");
      item.dataset.state = entry.state || "info";
      item.textContent = entry.label;
      els.reportChecks.appendChild(item);
    });

    els.resultReport.dataset.state = worried ? "warn" : "ok";
    els.reportSummary.textContent = worried
      ? "Contrôle qualité : points à vérifier"
      : "Contrôle qualité : conforme";
    els.resultReport.classList.toggle("hidden", checks.length === 0);
  }

  function finishWithError(message, hint) {
    state.busy = false;
    els.convertSpinner.classList.add("hidden");
    els.convertBtn.disabled = !state.file;
    els.progress.classList.add("hidden");
    showError(message, hint);
  }

  async function discardJob() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    if (state.jobId) {
      const jobId = state.jobId;
      state.jobId = null;
      try {
        await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
      } catch (error) {
        /* la purge automatique s'en chargera */
      }
    }
  }

  async function resetAll() {
    await discardJob();
    state.file = null;
    state.info = null;
    state.busy = false;

    els.convertBtn.disabled = true;
    els.convertSpinner.classList.add("hidden");
    els.filename.value = "";
    els.progress.classList.add("hidden");
    els.progressBar.style.width = "0%";
    els.progressPercent.textContent = "0 %";
    resetResult();
    els.resultReport.classList.add("hidden");
    clearError();
    setFileError("");
    showDropzone();
    els.dropzone.focus();
  }

  /* --- Écouteurs ------------------------------------------------------- */

  function bindEvents() {
    els.dropzone.addEventListener("click", () => {
      if (!state.busy) els.fileInput.click();
    });

    els.dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        els.dropzone.click();
      }
    });

    els.fileInput.addEventListener("change", () => {
      const [file] = Array.from(els.fileInput.files || []);
      els.fileInput.value = "";
      if (file) acceptFile(file);
    });

    ["dragenter", "dragover"].forEach((type) => {
      els.dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        els.dropzone.classList.add("is-dragging");
      });
    });

    ["dragleave", "dragend"].forEach((type) => {
      els.dropzone.addEventListener(type, () => els.dropzone.classList.remove("is-dragging"));
    });

    els.dropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("is-dragging");
      const [file] = Array.from(event.dataTransfer?.files || []);
      if (file) acceptFile(file);
    });

    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => event.preventDefault());

    els.changeFile.addEventListener("click", () => {
      if (!state.busy) els.fileInput.click();
    });

    els.convertBtn.addEventListener("click", convert);
    els.resetBtn.addEventListener("click", resetAll);

    [els.optFormat, els.optMargins, els.optFontSize, els.optLineHeight].forEach((select) => {
      select?.addEventListener("change", updateLayoutNote);
    });
    document.querySelectorAll(".preset").forEach((button) => {
      button.addEventListener("click", () => applyPreset(button.dataset.preset));
    });
  }

  bindEvents();
  updateLayoutNote();
  checkEngine();
})();
