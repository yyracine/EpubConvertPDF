# Reliure — convertisseur EPUB vers PDF

Application locale qui transforme un EPUB en PDF de qualité livre : mise en page A5,
sommaire paginé, signets navigables, en-têtes courants, numérotation, couverture et page
de titre. Aucun fichier ne quitte l'ordinateur.

## Fonctionnalités

- Lecture complète de l'EPUB avec **EbookLib** : métadonnées, ordre du spine, sommaire
  EPUB 2 (`toc.ncx`) et EPUB 3 (`nav.xhtml`), couverture, images, SVG, polices et CSS.
- Composition d'un document unique puis rendu **WeasyPrint** au format A5 (148 × 210 mm),
  marges 18 mm, corps 11 pt, césure française.
- Sommaire avec numéros de page réels (`target-counter`) et points de conduite.
- Signets PDF hiérarchiques issus des titres (`bookmark-level`).
- En-tête avec le titre du chapitre courant, pagination en pied de page.
- Neutralisation des mises en page d'écran de l'EPUB (sauts de page, `@page`, blocs
  positionnés, colonnes, tailles en unités de fenêtre) pour éviter pages blanches et
  texte rogné.
- Classification structurelle du livre (landmarks EPUB 3, guide EPUB 2, `epub:type`
  porté par un élément de niveau document, `linear="no"` et heuristiques de titres) :
  couverture, page de titre, mentions légales, sommaire, corps, notes et index. Les
  types posés sur un lien ou une portée en ligne sont ignorés : un simple renvoi
  interne ne peut pas faire écarter un chapitre. Les liminaires n'ont pas d'en-tête courant,
  le sommaire généré les omet, et les pages de titre ou couvertures internes qui
  doublent les pages générées sont supprimées.
- **Notes de bas de page** : les notes de l'EPUB (renvois `noteref`, liens vers une
  section de notes, `aside` typés) sont composées en bas de page avec renvoi cliquable
  et retour vers l'appel ; une section de notes vidée disparaît.
- Réglages de mise en page : format (A5, A4, 6 × 9 po, poche), marges, corps et
  interligne, avec les presets Roman, Essai et Manuel illustré. Les tailles de l'EPUB
  suivent le corps choisi, y compris ses tailles absolues : notes et légendes restent
  proportionnellement plus petites au lieu d'être figées.
- Pré-vol de l'analyse : mots, documents, liens internes, ressources, polices,
  sommaire et styles neutralisés.
- Rapport de contrôle qualité après conversion : pages vides, complétude du texte,
  signets, liens internes, polices incorporées, texte hors page et métadonnées.
- Interface française : dépôt de fichier, aperçu du livre, options, progression par
  étapes, diagnostic du moteur de rendu.

## Politique de saut de page

Les EPUB multiplient les sauts de page pour l'écran : `break-before: page` sur presque
chaque titre ou paragraphe, et parfois des pages blanches de respiration. Sur papier,
ces règles creusent des pages vides. Reliure applique donc une règle simple au CSS de
l'EPUB, avant la composition :

- **Sections conservées** : une règle de saut de page est gardée quand son sélecteur
  nomme une section éditoriale — appareil critique (`appcrit`, `apparatus`), annexes et
  appendices, notes, bibliographie, glossaire, index, supplément, postface, épilogue.
  Ces sections s'ouvrent sur une page neuve, comme dans un livre imprimé.
- **Pages blanches supprimées** : toutes les autres règles de saut (`break-before`,
  `break-after`, `page-break-before`, `page-break-after`) sont retirées, y compris
  celles de `.pageblanche` et des blocs d'identité de couverture. Chaque chapitre
  n'ouvre qu'une seule page : celle que Reliure pose lui-même sur `.chapter`.
- `@page`, les blocs `position: fixed/absolute/sticky`, les colonnes, les
  `overflow: hidden`, les tailles en `vh`/`vw` et les retraits hors écran sont également
  neutralisés.
- **Corps et interligne** : les `font-size` absolus de l'EPUB (`pt`, `px`, `mm`,
  mots-clés `small`, `large`…) sont convertis en `rem` par rapport au corps par défaut
  de 11 pt, y compris dans les attributs `style` et dans le raccourci `font`
  (`font: 12pt/1.4 Georgia, serif` devient
  `font: 1.0909rem Georgia, serif; line-height: inherit`). À 11 pt le rendu reste
  celui de l'EPUB ; à 12 pt toute la
  hiérarchie grandit dans la même proportion, si bien qu'un `p { font-size: 12pt }` ne
  fige plus le texte et qu'une note à 8 pt reste à deux tiers du corps. Le `rem` évite
  qu'une taille absolue imbriquée se multiplie en cascade. Les `line-height` figés
  (sans unité ou en unités absolues), ainsi que l'interligne éventuel d'un raccourci
  `font` à taille absolue, sont retirés pour que l'interligne choisi s'applique.

Mesure sur *Vous allez redécouvrir le management* : les 40 sections « Pour aller plus
loin » ouvrent une page et aucune page blanche n'apparaît ; la couverture, la page de
titre et le sommaire internes sont supprimés au profit des pages générées. *La Ferme
des Animaux* passe à 89 pages (page de titre interne supprimée).

## Notes de bas de page

Les notes de l'EPUB sont composées en bas de page (WeasyPrint `float: footnote`) au lieu
d'attendre le lecteur à la fin du livre :

- **Détection** : renvois `epub:type="noteref"` (ou `role="doc-noteref"`), liens
  internes qui pointent vers une note typée (`footnote`, `endnote`, `rearnote`) ou vers
  une section de notes, et `aside` typés restés dans le texte. Les liens de sommaire
  sont laissés tranquilles.
- **Composition** : le renvoi devient un numéro cliquable qui mène à la note, et la note
  porte un retour `↩` vers l'appel. Les numéros sont attribués par Reliure dans l'ordre
  du livre ; celui que l'EPUB écrit dans le texte de la note (« 12. », lien ou exposant)
  est retiré et la note affiche le sien. Le contenu est ramené en ligne (un bloc imbriqué
  couperait le paragraphe qui l'appelle) et ses blancs sont compactés : un saut de ligne
  dans une note fait échouer la composition de l'area `@footnote` de WeasyPrint.
  `footnote-policy: line` fait suivre la ligne d'appel à sa note quand celle-ci ne tient
  pas en bas de page, et le pseudo-appel de WeasyPrint reste présent bien que réduit à
  rien : le supprimer déplace des notes sur la page suivante.
- **Notes de fin** : quand toutes les notes d'une section passent en bas de page, la
  section est retirée, titre et entrée de sommaire compris. Les notes non appelées
  restent en fin de livre, de même qu'une note de plus de 1 200 caractères.
- L'identifiant d'origine du renvoi est conservé et les renvois d'une note à l'autre
  visent la note de bas de page correspondante : ces liens continuent d'aboutir.

Mesure sur *Vous allez redécouvrir le management* : 56 notes de fin passent en bas de
page, la section de notes disparaît et le PDF passe de 275 à 273 pages. *La Ferme des
Animaux*, sans note, reste à 89 pages.

## Prérequis

- **Python 3.10 ou plus récent** (testé avec 3.15).
- **Pango**, bibliothèque native requise par WeasyPrint (elle ne s'installe pas via pip).

### Installation de Pango sous Windows

```powershell
winget install MSYS2.MSYS2
```

Puis, dans un terminal **MSYS2 UCRT64** :

```bash
pacman -S --needed mingw-w64-ucrt-x86_64-pango
```

Reliure détecte automatiquement `C:\msys64\ucrt64\bin`. Si Pango est installé ailleurs,
définissez la variable d'environnement :

```powershell
setx WEASYPRINT_DLL_DIRECTORIES "C:\chemin\vers\bin"
```

## Lancement

Double-cliquez sur **`run.bat`** : le script crée l'environnement virtuel, installe les
dépendances au premier lancement, démarre le serveur sur `http://127.0.0.1:8765` et ouvre
le navigateur.

Installation manuelle équivalente :

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Variables optionnelles :

- `RELIURE_PORT` : port d'écoute (8765 par défaut).
- `RELIURE_OPEN_BROWSER=0` : ne pas ouvrir le navigateur au démarrage.

## Utilisation

1. Déposez un fichier `.epub` (100 Mo maximum, sans DRM) dans la zone prévue.
2. Vérifiez les métadonnées détectées et les éventuels avertissements.
3. Ajustez les options : sommaire et signets, numéros de page et en-têtes, couverture et
   page de titre, puis la mise en page (format, marges, corps, interligne) ou un preset
   Roman, Essai, Manuel illustré. Modifiez le nom du fichier si besoin.
4. Lancez la conversion : le rapport de contrôle qualité s'affiche avec le résultat, puis
   téléchargez le PDF. Les fichiers sont conservés une heure dans `output/jobs/` avant
   purge automatique.

## Tests

Tests de non-régression du rapport qualité : un PDF propre doit rester « ok », un PDF
forcé (page blanche et police non incorporée) doit passer en « points à vérifier » :

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Architecture

```
app.py                  Serveur Flask, validation, jobs
converter/
  epub_reader.py        EbookLib : métadonnées, chapitres, sommaire, couverture, zones, DRM
  assembler.py          HTML unique : ressources, CSS, sommaire, couverture, mise en page
  print.css             Feuille de style d'impression (A5, en-têtes, sommaire)
  renderer.py           Rendu WeasyPrint, diagnostic Pango, comptage de pages
  quality.py            Contrôle qualité du PDF produit
  jobs.py               Suivi de progression et purge
  errors.py             Erreurs métier en français
templates/index.html    Interface
static/style.css        Direction artistique
static/app.js           Interactions
tests/                  Tests de non-régression
```

### Routes

- `GET /` — interface
- `GET /api/health` — état de WeasyPrint et de Pango
- `POST /api/inspect` — `file` → métadonnées, couverture, avertissements
- `POST /api/convert` — `file`, `toc`, `page_numbers`, `cover_page`, `filename` → job
- `GET /api/jobs/<id>` — progression
- `GET /api/jobs/<id>/download` — PDF
- `DELETE /api/jobs/<id>` — nettoyage immédiat

## Limites connues

- Les EPUB protégés par un **DRM** sont refusés avec un message explicite.
- Les EPUB à **mise en page fixe** (`rendition:layout: pre-paginated`) sont convertis au
  mieux ; un avertissement est affiché.
- Le JavaScript et les contenus interactifs de l'EPUB ne sont pas repris (comportement
  normal en PDF).
- Les formats, marges, corps et interlignes proposés sont des presets ; les valeurs
  sur mesure ne sont pas exposées.

## Dépannage

- **« Le moteur de rendu PDF n'est pas disponible »** : Pango est absent du système ou
  introuvable. Suivez la section Pango ci-dessus, puis relancez Reliure.
- **Port déjà utilisé** : lancez `set RELIURE_PORT=8766` puis `run.bat`.
- **Fichier refusé (413)** : l'EPUB dépasse 100 Mo.
