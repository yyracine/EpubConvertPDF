"""Non-régression de la livraison : `tools/copier_livres.py` sur des dossiers temporaires.

Couvre les cas qui ont motivé le script (copie manquante, copie périmée silencieuse) et
ses codes de retour 0 (à jour), 1 (écart ou échec), 2 (source absente). Aucun fichier réel
de `output/livres` ni de `livres-pdf` n'est touché.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import copier_livres

_NOUVEAU = b"%PDF-1.4 version nouvelle"
_ANCIEN = b"%PDF-1.4 version ancienne"


class CopierLivresTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        racine = Path(self._tmp.name)
        self.source = racine / "livres"
        self.cible = racine / "livres-pdf"
        self.source.mkdir()

    def _ecrire(self, dossier: Path, nom: str, contenu: bytes) -> Path:
        dossier.mkdir(parents=True, exist_ok=True)
        chemin = dossier / nom
        chemin.write_bytes(contenu)
        return chemin

    def _lancer(self, *args: str) -> tuple[int, str]:
        """Exécute le script sur les dossiers temporaires et rend (code, sortie)."""
        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie), contextlib.redirect_stderr(sortie):
            code = copier_livres.main(
                ["--source", str(self.source), "--cible", str(self.cible), *args]
            )
        return code, sortie.getvalue()

    def test_copie_manquante_est_copiee(self) -> None:
        self._ecrire(self.source, "barbecue.pdf", b"%PDF-1.4 barbecue")
        self._ecrire(self.source, "jimi.pdf", b"%PDF-1.4 jimi")

        code, sortie = self._lancer()

        self.assertEqual(code, 0)
        self.assertIn("manquantes  : 2", sortie)
        self.assertEqual((self.cible / "barbecue.pdf").read_bytes(), b"%PDF-1.4 barbecue")
        self.assertEqual((self.cible / "jimi.pdf").read_bytes(), b"%PDF-1.4 jimi")

    def test_copie_perimee_detectee_puis_remplacee(self) -> None:
        self._ecrire(self.source, "barbecue.pdf", _NOUVEAU)
        self._ecrire(self.cible, "barbecue.pdf", _ANCIEN)

        code, sortie = self._lancer("--verifier")
        self.assertEqual(code, 1)
        self.assertIn("périmées    : 1", sortie)
        self.assertEqual((self.cible / "barbecue.pdf").read_bytes(), _ANCIEN)

        code, sortie = self._lancer()
        self.assertEqual(code, 0)
        self.assertIn("copiées     : 1", sortie)
        self.assertEqual((self.cible / "barbecue.pdf").read_bytes(), _NOUVEAU)

        code, sortie = self._lancer("--verifier")
        self.assertEqual(code, 0)
        self.assertIn("déjà à jour : 1", sortie)

    def test_code_2_si_source_absente(self) -> None:
        self.source.rmdir()

        code, sortie = self._lancer()

        self.assertEqual(code, 2)
        self.assertIn("Dossier source introuvable", sortie)

    def test_code_1_si_aucun_pdf(self) -> None:
        code, sortie = self._lancer()

        self.assertEqual(code, 1)
        self.assertIn("Aucun PDF", sortie)

    def test_copie_orpheline_signalee_mais_conservee(self) -> None:
        self._ecrire(self.source, "barbecue.pdf", _NOUVEAU)
        self._ecrire(self.cible, "barbecue.pdf", _NOUVEAU)
        self._ecrire(self.cible, "ancien-tirage.pdf", _ANCIEN)

        code, sortie = self._lancer("--verifier")

        self.assertEqual(code, 1)
        self.assertIn("orphelines", sortie)
        self.assertTrue((self.cible / "ancien-tirage.pdf").exists())


if __name__ == "__main__":
    unittest.main()
