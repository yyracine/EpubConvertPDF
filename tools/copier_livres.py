"""Copie les PDF produits par Reliure vers le dossier de livraison, avec contrôle MD5.

Usage :
    python tools/copier_livres.py              # copie ce qui manque ou a changé
    python tools/copier_livres.py --verifier   # ne copie rien, signale les écarts

Une copie présente mais différente de la source est signalée comme **périmée** puis
remplacée. Les PDF du dossier de livraison qui n'existent plus dans `output/livres`
sont signalés comme orphelins ; ils ne sont jamais supprimés.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "output" / "livres"
DEFAULT_TARGET = Path("D:/Documents/DEV/Test/livres-pdf")


def md5(path: Path) -> str:
    """Empreinte MD5 d'un fichier, lue par blocs pour ne pas charger 100 Mo en mémoire."""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copie les PDF de output/livres vers le dossier de livraison, avec contrôle MD5."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="dossier des PDF produits")
    parser.add_argument(
        "--cible", "--target", dest="target", type=Path, default=DEFAULT_TARGET,
        help="dossier de livraison",
    )
    parser.add_argument(
        "--verifier", action="store_true",
        help="ne copie rien, signale seulement les copies manquantes ou périmées",
    )
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"Dossier source introuvable : {args.source}", file=sys.stderr)
        return 2

    sources = sorted(args.source.glob("*.pdf"))
    if not sources:
        print(f"Aucun PDF dans {args.source}", file=sys.stderr)
        return 1

    if not args.verifier:
        args.target.mkdir(parents=True, exist_ok=True)

    a_jour: list[str] = []
    perimees: list[str] = []
    manquantes: list[str] = []
    copiees: list[str] = []
    echecs: list[tuple[str, str]] = []

    for source in sources:
        cible = args.target / source.name
        empreinte = md5(source)
        if cible.exists():
            if md5(cible) == empreinte:
                a_jour.append(source.name)
                continue
            perimees.append(source.name)
        else:
            manquantes.append(source.name)

        if args.verifier:
            continue
        try:
            shutil.copy2(source, cible)
        except OSError as exc:
            indice = " (fichier ouvert dans un lecteur PDF ?)" if getattr(exc, "winerror", None) == 32 else ""
            echecs.append((source.name, f"{exc.strerror or exc}{indice}"))
            continue
        if md5(cible) != empreinte:
            echecs.append((source.name, "copie corrompue : MD5 différent après copie"))
        else:
            copiees.append(source.name)

    orphelines = [
        fichier.name
        for fichier in sorted(args.target.glob("*.pdf"))
        if fichier.name not in {source.name for source in sources}
    ]

    print(f"Source : {args.source}")
    print(f"Cible  : {args.target}")
    print(f"PDF source : {len(sources)}")
    if a_jour:
        print(f"  déjà à jour : {len(a_jour)}")
    if perimees:
        print(f"  périmées    : {len(perimees)}")
        for name in perimees:
            print(f"      - {name}")
    if manquantes:
        print(f"  manquantes  : {len(manquantes)}")
        for name in manquantes:
            print(f"      - {name}")
    if copiees:
        print(f"  copiées     : {len(copiees)}")
        for name in copiees:
            print(f"      - {name}")
    if echecs:
        print(f"  échecs      : {len(echecs)}")
        for name, raison in echecs:
            print(f"      - {name} : {raison}")
    if orphelines:
        print(f"  orphelines dans la cible (absentes de la source, non supprimées) : {len(orphelines)}")
        for name in orphelines:
            print(f"      - {name}")

    if args.verifier:
        if perimees or manquantes:
            print("\nMode vérification : relancer sans --verifier pour mettre à jour.")
            return 1
        print("\nMode vérification : toutes les copies sont à jour.")
        return 0 if not orphelines else 1

    if echecs:
        return 1
    print(f"\n{len(copiees)} fichier(s) copié(s), {len(a_jour)} déjà à jour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
