"""Exceptions métier, converties en messages compréhensibles par l'utilisateur."""

from __future__ import annotations


class ConversionError(Exception):
    """Erreur de conversion destinée à l'interface."""

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status


class DependencyError(ConversionError):
    """Le moteur de rendu ne peut pas démarrer (bibliothèques natives manquantes)."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message, hint=hint, status=503)
