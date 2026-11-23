# AGENTS.md — Reliure (EPUB → PDF)

## À faire en premier

**Lire `RESUME.md` avant toute intervention**, puis le mettre à jour à la fin de la session
(date, travail effectué, points restants). C'est le journal de bord du projet.

## Projet

Application locale Flask + WeasyPrint qui transforme un EPUB en PDF de qualité livre
(A5, sommaire paginé, signets, en-têtes courants, notes de bas de page). Interface
française, aucun fichier ne quitte la machine, pas d'étape de build côté front (HTML/CSS/JS
vanilla).

Dossier : `D:\Documents\Dev\Freebuff\ConvertisseurEpubPdf`. Ce n'est pas le workspace
connecté : les outils fichier doivent utiliser des **chemins absolus** et le terminal doit
travailler depuis `/d/Documents/Dev/Freebuff/ConvertisseurEpubPdf`.
Dépôt : <https://github.com/yyracine/EpubConvertPDF>, branche `main`. Les livres d'essai
(`*.epub`), `output/` et `.venv/` sont ignorés : ils restent locaux et ne doivent pas être
ajoutés au dépôt (livres sous droits).

## Commandes

```bash
# lancement (run.bat fait venv + dépendances + navigateur)
.venv/Scripts/python.exe app.py                  # http://127.0.0.1:8765

# lancement sans navigateur, en arrière-plan
RELIURE_PORT=8765 RELIURE_OPEN_BROWSER=0 .venv/Scripts/python.exe app.py > output/server.log 2>&1 &
curl -s http://127.0.0.1:8765/api/health         # ready: true + version WeasyPrint

# tests et contrôles
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m compileall -q converter tests app.py
node --check static/app.js
```

`print.css` et les constantes sont lus à l'import : **redémarrer le serveur** après une
modification. Retrouver le processus avec
`netstat -ano | grep -E "127.0.0.1:8765\s.*LISTENING"`, vérifier sa ligne de commande
(`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'ProcessId=…'"`)
avant `taskkill //PID … //F`.

## Environnement

- Python ≥ 3.10 (`.venv` du projet en 3.15) ; dépendances `requirements.txt` :
  Flask 3, EbookLib, WeasyPrint 62–70, lxml, pypdf, Pillow.
- Pango requis par WeasyPrint : MSYS2 UCRT64 (`winget install MSYS2.MSYS2` puis
  `pacman -S mingw-w64-ucrt-x86_64-pango`). `C:\msys64\ucrt64\bin` est détecté
  automatiquement, sinon `WEASYPRINT_DLL_DIRECTORIES`.
- `output/` est gitignoré (jobs, journaux, artefacts de contrôle). Les livres d'essai sont
  à la racine : *La Ferme des Animaux* (roman, sans note) et *Vous allez redécouvrir le
  management* (essai, 56 notes de fin).

## Routes

- `GET /` — interface
- `GET /api/health` — diagnostic Pango / WeasyPrint
- `POST /api/inspect` — analyse d'un EPUB (métadonnées, pré-vol, options)
- `POST /api/convert` — lance un job de conversion
- `GET /api/jobs/<id>` — progression, étapes, rapport qualité
- `GET /api/jobs/<id>/download` — PDF produit

## Fichiers clés

- `converter/css_guard.py` — nettoyage du CSS de l'EPUB, conversion des tailles en `rem`,
  `DEFAULT_FONT_PT`, raccourci `font`.
- `converter/assembler.py` — HTML unique, ordre des chapitres, sommaire, notes de bas de
  page, `Options`, `DEFAULT_LINE_HEIGHT`.
- `converter/epub_reader.py` — métadonnées, spine, sommaire, couverture, DRM, classification.
- `converter/print.css` — mise en page d'impression (page, en-têtes, folios, notes).
- `converter/renderer.py`, `converter/quality.py`, `converter/jobs.py`, `converter/errors.py`.
- `app.py`, `templates/index.html`, `static/` — serveur et interface.
- `tests/` — `test_layout_and_structure.py`, `test_footnotes.py`, `test_quality_report.py`.

## Conventions à respecter

- Code, commentaires, messages et interface **en français** ; accords et typographie
  soignés (espaces insécables avant `:` `;` `!` `?`).
- Le corps et l'interligne choisis priment sur le CSS de l'EPUB : `!important` sur
  `html, body { font-size }` et `body { line-height }`, et ne pas retirer les règles de
  `css_guard` qui neutralisent les `line-height` figés.
- Les tailles absolues de l'EPUB sont converties en **`rem`** référencés à
  `DEFAULT_FONT_PT` (11 pt). Ne pas réintroduire de littéraux `11.0` / `1.55` : utiliser
  `DEFAULT_FONT_PT` (css_guard) et `DEFAULT_LINE_HEIGHT` (assembler).
- Classification : seuls les `epub:type` portés par un élément **de niveau document**
  comptent (`html`, `body`, `main`, `section`, `article`, `nav`, `header`, `div`), jamais
  ceux d'un lien ou d'une portée en ligne.
- Notes de bas de page : `<a class="fn-call">` suivi d'un `<span class="footnote reliure-note">`
  **dans le paragraphe**, avant le texte qui suit l'appel ; numéro attribué par Reliure ;
  identifiant d'origine de l'appel conservé ; contenu ramené en ligne ; blancs compactés.
- **Ne pas supprimer le pseudo-appel `::footnote-call`** (il est réduit à rien) : sa
  présence rattache les notes à leur page ; ne pas déplacer les notes en fin ou en tête de
  paragraphe (mesuré : plus de notes décalées).

## Pièges connus

- Un saut de ligne dans le texte d'une note fait planter WeasyPrint
  (`Got ' \n' between two lines`) dès que l'area `@footnote` se coupe.
- Un bloc (`<p>`, `<div>`…) imbriqué dans un `<span>` de note coupe le paragraphe appelant.
- WeasyPrint 70 laisse encore 2 notes sur 56 sur la page suivante (détail dans `RESUME.md`).
- Un `line-height` ou `font` figé dans l'EPUB fige le réglage : passer par `css_guard`.
- `float: footnote` exige une version récente de WeasyPrint ; le rendu PDF ne doit pas être
  remplacé par une capture d'écran.

## Vérification

- Tests unitaires obligatoires après toute modification de `converter/` ou `app.py`.
- Changement de mise en page : mesurer sur les deux EPUB d'essai (pages, tailles de police,
  positions) et, si possible, regarder les pages réellement rendues — `pypdf` pour les
  mesures, `pypdfium2` installé temporairement (`pip install --target output/_pdfview
  pypdfium2`, puis supprimé) pour rasteriser, artefacts dans `output/_visual/`.
- Toujours rapporter ce qui a été vérifié et ce qui ne l'a pas été (interface, rendu visuel).
