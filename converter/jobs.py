"""Suivi des conversions lancées en arrière-plan."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

JOB_TTL_SECONDS = 3600


@dataclass
class Job:
    """Une conversion : son avancement, son résultat et son dossier de travail."""

    id: str
    directory: Path
    filename: str = "document.pdf"
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    message: str = "En attente"
    error: str | None = None
    hint: str | None = None
    pages: int | None = None
    size: int | None = None
    report: dict | None = None
    created: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, **changes) -> None:
        with self.lock:
            for key, value in changes.items():
                setattr(self, key, value)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "progress": self.progress,
                "message": self.message,
                "error": self.error,
                "hint": self.hint,
                "pages": self.pages,
                "size": self.size,
                "report": self.report,
                "filename": self.filename,
                "download_url": f"/api/jobs/{self.id}/download" if self.status == "done" else None,
            }


class JobStore:
    """Registre en mémoire, avec purge des conversions anciennes."""

    def __init__(self, root: Path, ttl: int = JOB_TTL_SECONDS) -> None:
        self.root = root
        self.ttl = ttl
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job_id = uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, directory=directory)
        with self._lock:
            self._jobs[job_id] = job
        self.purge()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        shutil.rmtree(job.directory, ignore_errors=True)
        return True

    def purge(self) -> None:
        now = time.time()
        with self._lock:
            expired = [jid for jid, job in self._jobs.items() if now - job.created > self.ttl]
        for job_id in expired:
            self.delete(job_id)
        for folder in self.root.glob("*"):
            try:
                if folder.is_dir() and now - folder.stat().st_mtime > self.ttl:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                continue
