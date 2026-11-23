# Reliure — résumé du projet

> **À lire au début de chaque session, à mettre à jour à la fin.**
> Les instructions de travail (commandes, conventions, pièges) sont dans `AGENTS.md`.
> Dernière mise à jour : 23 novembre 2026.

Convertisseur local EPUB → PDF « de qualité livre » (Flask + WeasyPrint 70, Python 3.15,
interface française). Aucun fichier ne quitte l'ordinateur.
Dossier : `D:\Documents\Dev\Freebuff\ConvertisseurEpubPdf` — ce n'est **pas** le workspace
connecté (`D:\Documents\Dev\Test`) : utiliser des chemins absolus pour lire/écrire, et
travailler en terminal depuis `/d/Documents/Dev/Freebuff/ConvertisseurEpubPdf`.
Dépôt : <https://github.com/yyracine/EpubConvertPDF>, branche `main` ; les livres d'essai
(`*.epub`), `output/` et `.venv/` sont ignorés (livres sous droits, non publiés).

---

## 1. État actuel

- Chaîne complète : lecture EbookLib → assemblage HTML unique → rendu WeasyPrint → contrôle
  qualité. Interface web : dépôt, aperçu, options, progression, rapport.
- Mise en page : A5/A4/6×9/poche, marges, corps, interligne, presets ; sommaire paginé,
  signets, en-têtes courants, folios, couverture et page de titre générées.
- Nettoyage du CSS de l'EPUB : sauts de page, `@page`, blocs positionnés, colonnes,
  `overflow`, unités de fenêtre, retraits hors écran ; **corps et interligne imposés**,
  les tailles de l'EPUB suivant le corps choisi.
- Classification des documents (couverture, titre, mentions, sommaire, corps, notes, index)
  à partir des types **de niveau document** uniquement.
- **Notes de bas de page réelles** (`float: footnote`) avec appel cliquable et retour.
- Serveur de développement en cours sur `http://127.0.0.1:8765` (`/api/health` → `ready: true`).

Vérifications de référence (23/11/2026) :

| Contrôle | Résultat |
|---|---|
| `.venv/Scripts/python.exe -m unittest discover -s tests -v` | **14 tests OK** |
| `compileall` (converter, tests, app.py) et `node --check static/app.js` | OK |
| *La Ferme des Animaux* (11 pt / 1,55) | 89 p., aucune note |
| *Management* (11 pt / 1,55) | 274 p., 56 notes en bas de page, section de notes retirée |

---

## 2. Ce qui a été fait, session par session

### Session A — corps et interligne réellement appliqués
- `converter/css_guard.py` : rejet des `font-size` en unités absolues et des `line-height`
  figés (sans unité ou absolus), y compris dans les attributs `style` ; les valeurs
  relatives (`em`, `%`, `rem`, `ex`, `ch`, mots-clés relatifs, `0`) sont conservées.
- `converter/assembler.py` : `font-size`/`line-height` de `_layout_css` posés en
  `!important` pour qu'un `!important` de l'EPUB ne reprenne pas le dessus.
- `converter/epub_reader.py` : `_content_types` parcourt l'arbre HTML ; seuls `html`,
  `body`, `main`, `section`, `article`, `nav`, `header`, `div` portent un type, et les
  éléments imbriqués dans un `<a>` sont exclus (un renvoi interne ne peut plus écarter un
  chapitre).
- Nouveau `tests/test_layout_and_structure.py` (EPUB 3 minimal écrit à la main, pour garder
  les attributs `epub:type` intacts).
- Mesures avant/après sur un EPUB agressif : rapports 1,00 → 1,20 (corps) et 1,21 (interligne).

### Session B — tailles absolues mises à l'échelle
- Remplacé la suppression par une conversion en `rem` : `12pt` → `1.0909rem`,
  `8pt` → `0.7273rem`, `12px` → `0.8182rem`, mots-clés absolus (`small`…`xxx-large`).
  Référence : `DEFAULT_FONT_PT = 11.0` ; les unités `pt/px/pc/mm/cm/in/q` sont couvertes.
- Pourquoi `rem` : pas de multiplication en cascade des tailles imbriquées, et la
  hiérarchie du livre grandit avec le corps choisi (une note à 8 pt reste à 2/3 du corps).
- Les `line-height` figés restent retirés ; `calc()` et valeurs non calculables aussi.
- Tests : proportion légende/texte conservée (8/12) à 10 et 12 pt, légende qui grandit de 20 %.
- README mis à jour (conversion, proportions).

### Session C — raccourci `font` et partage de la constante
- `font: 12pt/1.4 Georgia, serif` → `font: 1.0909rem Georgia, serif; line-height: inherit`
  (le raccourci remet `line-height` à `normal`, d'où le `inherit`).
  Gère `!important`, les familles entre guillemets, les tailles relatives (laissées
  intactes) et les raccourcis incomplets (`font: 12pt` → `font-size`).
- `REFERENCE_PT` renommé `DEFAULT_FONT_PT` (source unique) et importé par `assembler.py`
  pour `Options.font_size`, `_FONT_SIZES` et le repli de `_layout_css`.

### Session D — test d'interligne et simplification
- Test `test_shorthand_line_height_does_not_override_setting` : ratio 1,7/1,4 retrouvé
  (1,214 avec correctif, 1,000 avec l'ancien comportement — vérifié par simulation).
- Simplifications : `_rewrite_font_shorthand` + `_is_font_size` fusionnés en
  `_font_shorthand` (liste de déclarations, `!important` ajouté à un seul endroit) ;
  ajout de `DEFAULT_LINE_HEIGHT = 1.55` dans `assembler.py` ; `app.py` utilise
  `Options.font_size` / `Options.line_height` au lieu de littéraux.

### Session E — notes de bas de page
- `converter/assembler.py`, section « Notes de bas de page » :
  - détection : renvois `epub:type="noteref"` / `role="doc-noteref"`, tout lien interne vers
    une note typée (`footnote`, `endnote`, `rearnote`, `note`) ou vers un chapitre classé
    `notes`, et `aside` typés restés dans le texte (type, `role`, ou classe
    `footnote`/`endnote`/`note`/`fn`) ; les liens de `<nav>` sont ignorés ;
  - composition : `<a class="fn-call" id="<id d'origine>" href="#reliure-fn-N">N</a>` suivi
    de `<span class="footnote reliure-note" id="reliure-fn-N">` contenant la note et un
    retour `<a class="fn-back" href="#<id d'origine>">↩</a>` ;
  - le contenu d'une note de fin est **ramené en ligne** (`_inline_markup`) : un bloc
    imbriqué fermerait le paragraphe appelant ;
  - **blancs compactés** : un saut de ligne dans une note déclenche une assertion interne
    de WeasyPrint (`Got ' \n' between two lines`) dès que l'area `@footnote` se coupe ;
  - le texte qui suit l'appel (`tail`) est déplacé après la note : sinon la note flotte en
    fin de paragraphe, donc de page ;
  - une section de notes dont toutes les notes passent en bas de page est retirée (titre et
    entrée de sommaire compris) ; notes non appelées et notes > **1 200 caractères**
    (`_MAX_FOOTNOTE_CHARS`) restent en fin de livre ;
  - `note_ids` : l'ancien identifiant d'une note est remappé vers `#reliure-fn-N` par
    `_rewrite_links`, donc les renvois d'une note à l'autre aboutissent.
- `converter/print.css` : `@footnote` (filet, `0.8em`, interligne 1.3, cadré à gauche),
  `.footnote` (`float: footnote`, `display: block !important`, `footnote-policy: line`,
  `margin-top: 0.35em`), pseudo-appel WeasyPrint conservé mais réduit à rien
  (`content: counter(footnote); font-size: 0`), `.fn-num` en demi-gras, `.fn-call`
  en exposant, `.fn-back` discret.
- `tests/test_footnotes.py` : note en bas de la page de son appel (paragraphe non coupé),
  appel et retour cliquables, `aside` typé converti, lien croisé ordinaire intact, lien
  note → note aboutissant à `#reliure-fn-2`.
- README : fonctionnalité + section « Notes de bas de page ».

### Session F — contrôle visuel et corrections
- Rendu réel de *Management* rasterisé (pypdfium2 installé temporairement dans
  `output/_pdfview`, puis supprimé) et pages regardées : p. 19 (3 notes), p. 85 (note de
  1 072 caractères), p. 96/97 (appel en bas de page).
- Corrigé : la note affichait le numéro de l'EPUB (« 1. ») alors que l'appel disait « 33 ».
  Le numéro de l'EPUB (texte `12.`, lien `<a>14</a>.` ou `<sup>1</sup>`) est retiré et la
  note affiche le numéro de Reliure (`_strip_note_number`, `.fn-num`).
- Corrigé : notes collées → `margin-top: 0.35em`.
- Corrigé : notes rejetées à la page suivante, 12/56 → **2/56** grâce à
  `footnote-policy: line` + pseudo-appel invisible (mesuré en comparant, sur le PDF, la page
  de chaque appel à la page de sa destination nommée).

---

## 3. Ce qui reste à faire

1. **2 notes sur 56 encore rejetées à la page suivante** (fn-35 p. 96→97, fn-49 p. 247→248) :
   limite de WeasyPrint 70, qui réserve la place puis déplace la note malgré
   `footnote-policy: line`. Piste : rendu en deux passes avec re-pagination ciblée — plus
   lourd, peut creuser des blancs ; non fait sans accord.
2. **Raccourci `font` à taille relative** (`font: 0.9em/1.2 …`) : laissé intact, donc son
   interligne figé subsiste. Hors périmètre de la demande précédente, à normaliser si besoin.
3. **Interface web non revérifiée visuellement** depuis les changements (aucun parcours dans
   le navigateur, aucune capture).
4. **Notes non appelées** : restent imprimées en fin de livre (pas d'option pour les
   remonter en bas de page).
5. **Seuil `_MAX_FOOTNOTE_CHARS` (1 200)** : arbitraire, à ajuster selon les retours.
6. **Rapport de pré-vol / qualité** : ne mentionne pas encore le nombre de notes détectées ni
   les notes laissées en fin de livre (information utile à l'utilisateur).

---

## 4. Décisions techniques à ne pas défaire

- **`rem` pour les tailles absolues**, référencées à `DEFAULT_FONT_PT` (11 pt) : à 11 pt le
  rendu reste celui de l'EPUB, ailleurs la hiérarchie suit le corps ; pas de cascade.
- **`DEFAULT_FONT_PT` (css_guard) et `DEFAULT_LINE_HEIGHT` (assembler) = sources uniques** ;
  ne pas réintroduire de littéraux `11.0` / `1.55`.
- **`!important`** sur `html, body { font-size }` et `body { line-height }` : nécessaire face
  aux EPUB qui marquent leurs tailles ainsi.
- **Notes en `<span>` dans le paragraphe**, après l'appel et avant son texte : placement sur
  la bonne page et paragraphe intact.
- **Ne pas retirer le pseudo-appel `::footnote-call`** : sa présence (même vide) conditionne
  le rattachement des notes à leur page.
- **Ne pas déplacer les notes en fin de paragraphe ni en tête de paragraphe** : mesuré,
  cela augmente nettement les notes décalées.
- Les blancs du texte des notes doivent rester compactés (sinon WeasyPrint plante).

---

## 5. Commandes utiles

```bash
# serveur (depuis le dossier du projet)
RELIURE_PORT=8765 RELIURE_OPEN_BROWSER=0 .venv/Scripts/python.exe app.py > output/server.log 2>&1 &
curl -s http://127.0.0.1:8765/api/health

# tests et contrôles
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m compileall -q converter tests app.py
node --check static/app.js
```

Retrouver/arrêter le serveur : `netstat -ano | grep -E "127.0.0.1:8765\s.*LISTENING"`, puis
vérifier la ligne de commande du PID
(`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'ProcessId=…'"`)
avant `taskkill //PID … //F`. `print.css` est lu à l'import : redémarrer après modification.

Mesures PDF : `pypdf` (`extract_text(visitor_text=…)` donne `font_size` et `tm`), destinations
nommées via `reader.named_destinations` ; rasterisation éventuelle avec `pypdfium2` installé
temporairement (`pip install --target output/_pdfview pypdfium2`), puis dossier supprimé.

Livres d'essai à la racine : *La Ferme des Animaux* (roman, sans note) et *Vous allez
redécouvrir le management* (essai, 56 notes de fin).

---

## 6. Fichiers clés

| Fichier | Rôle |
|---|---|
| `converter/css_guard.py` | Neutralisation du CSS EPUB, conversion des tailles en `rem`, `DEFAULT_FONT_PT`, raccourci `font` |
| `converter/assembler.py` | HTML unique, ordre des chapitres, sommaire, classification appliquée, notes de bas de page, `Options` |
| `converter/epub_reader.py` | Métadonnées, spine, sommaire, couverture, DRM, classification (types de niveau document) |
| `converter/print.css` | Styles d'impression : page, en-têtes, folios, sommaire, notes de bas de page |
| `converter/renderer.py` / `quality.py` | Rendu WeasyPrint et contrôle qualité du PDF |
| `converter/jobs.py` / `errors.py` | Suivi de progression, erreurs en français |
| `app.py`, `templates/index.html`, `static/` | Serveur Flask et interface |
| `tests/test_layout_and_structure.py` | Corps/interligne, raccourci `font`, classification |
| `tests/test_footnotes.py` | Notes : placement, liens, `aside` typé, note → note |
| `tests/test_quality_report.py` | Rapport qualité |
| `output/_visual/` | Artefacts du dernier contrôle visuel (`management.pdf`, `note-019/085/096/097.png`) |

---

## 7. Journal des mises à jour

- **23/11/2026** — création du fichier ; reprise des sessions A à F (corps/interligne,
  tailles en `rem`, raccourci `font`, notes de bas de page, contrôle visuel et corrections).
  14 tests OK, serveur relancé, 2 notes encore décalées sur 56.
- **23/11/2026** — création d'`AGENTS.md` : protocole de session (lire ce fichier d'abord,
  le mettre à jour à la fin), commandes, environnement, conventions et pièges connus.
- **23/11/2026** — premier commit (`972cf66`) poussé sur
  <https://github.com/yyracine/EpubConvertPDF> (branche `main`) : 22 fichiers, `.gitignore`
  étendu à `*.epub` pour garder les livres d'essai hors du dépôt ; identité git locale
  réglée sur `yyracine <yyracine@users.noreply.github.com>`.
