# MedAiCR — anonymisation et aide à la rédaction de comptes rendus médicaux

Application web **locale** qui **anonymise** des comptes rendus (CR) médicaux PDF,
puis — **optionnellement** — aide à les **rédiger** via une IA, à partir du
**texte anonymisé** uniquement.

> ## ⚠️ Avertissement — outil pédagogique, PAS un dispositif médical
>
> **Cet outil est fourni à des fins UNIQUEMENT PÉDAGOGIQUES et de démonstration.**
>
> - Ce **n'est pas un dispositif médical (DM)** au sens du Règlement (UE)
>   2017/745. Il n'est ni certifié, ni marqué CE, ni destiné à un usage clinique
>   ou diagnostique.
> - Il **ne doit pas** être utilisé pour la prise en charge de patients réels, ni
>   pour produire des comptes rendus à visée médicale opposable.
> - L'anonymisation repose sur des règles heuristiques **sans garantie
>   d'exhaustivité** : aucune assurance que toutes les données identifiantes
>   soient retirées sur un format de document non prévu.
> - La génération de CR par IA peut produire des **erreurs** ; tout contenu doit
>   être vérifié par un professionnel. Aucune responsabilité de l'auteur ne
>   saurait être engagée du fait de son utilisation.

> **Modèle de confidentialité**
> - L'**anonymisation** est faite **100 % en local** (extraction PDF + masquage
>   par règles/zones dans le serveur Python local). Aucune donnée patient ne sort
>   du poste.
> - La **génération de CR** est **optionnelle** : si tu l'utilises, c'est le texte
>   **déjà anonymisé** (`[NOM]`, `[DATE]`, `[ID]`…) qui est envoyé au fournisseur
>   d'IA choisi. Les clés API restent stockées localement.

---

## Sommaire des fonctionnalités

| | Fonction |
|---|---|
| 🗂 | **Workflow** : liste de travail des examens des dossiers surveillés, anonymisés en tâche de fond, triés par **heure d'arrivée**, avec **liséré coloré par type** |
| ✍ | **Manuel** : anonymise un PDF ou un texte collé à la demande, aperçu avant/après |
| 🖌 | **Anonymisation par zones (pinceau)** : sélection **visuelle** des zones à masquer sur le PDF, pré-remplie par l'IA, multi-pages |
| ➕ | **Types de documents personnalisés** : appris à partir d'un CR **fictif** (IA), puis **éditables** ; un **PDF de référence** est stocké |
| 🧠 | **Génération de CR par IA** multi-fournisseurs (DeepSeek / OpenAI / Anthropic), un **system prompt par type** |
| 📄 | **Courrier Word** : export `.rtf` du CR avec les **valeurs importantes en gras** |
| 🎨 | **Couleurs par type** d'examen, paramétrables |
| 🛡 | **Métadonnées effacées** (titre, auteur, XMP, signets, pièces jointes, formulaires) |
| 💾 | **Sauvegarde / restauration** de toute la configuration en un fichier |
| 👀 | **Surveillance automatique de dossiers** : crée des `ANOM_*.pdf` en tâche de fond |

---

## Anonymisation : ce qui est retiré / conservé

**Retiré** : nom du patient, n° patient / dossier, date de naissance, âge, sexe,
toutes les dates calendaires et horodatages date+heure, médecin
référent/opérateur, établissement, e-mail, téléphone, NIR — **et toutes les
métadonnées du fichier** (voir plus bas).

**Conservé** : toutes les mesures et conclusions, taille / poids / IMC / SC, et
les **heures « nues »** (`11:47`, durées d'épisodes, durée d'enregistrement) —
elles portent souvent une information clinique et ne sont pas identifiantes une
fois le nom et la date de naissance retirés.

> **Méthode** (100 % locale, anonymisation *pure* — jetons fixes, aucune table de
> correspondance conservée) :
> 1. **Extraction par libellés** : on lit la valeur dans son champ étiqueté
>    (ex. `Nom : …`) puis on la remplace **partout** dans le document.
> 2. **Zones dessinées** (optionnel) : le texte sous une zone est lu et masqué
>    partout, **et** le rectangle est noirci (couvre aussi logos/tampons sans
>    texte). Voir « Anonymisation par zones ».
> 3. **Règles regex génériques** : dates, âge, sexe, téléphone, e-mail, NIR,
>    médecin/établissement résiduels.
> 4. **Rédaction du PDF** (PyMuPDF) : mise en page conservée, texte sous-jacent
>    supprimé (non extractible).

### Types de CR pris en charge nativement

| Type | Appareil | Identifiants retirés (spécifiques) |
|------|----------|-------------------------------------|
| Échographie cardiaque | Philips | Nom (en-tête + pieds de page), N° patient, date d'étude |
| Polygraphie ventilatoire | Nox T3 (+ variantes) | Nom (maj./min.), date de naissance, dates |
| Holter ECG | Schiller MT-200 | Nom (en-têtes EN/FR), date de naissance, horodatages |

D'autres types s'ajoutent via l'assistant **« Gestion de document »** (ci-dessous).

### Métadonnées effacées

À chaque rédaction, **toutes les métadonnées** du PDF sont supprimées (défense en
profondeur — des appareils y inscrivent parfois le nom/identifiant patient hors
du texte visible) : champs `/Info` (titre, auteur, sujet, mots-clés, créateur,
producteur, dates), bloc **XMP**, **signets**, **pièces jointes** embarquées, et
**champs de formulaire**.

---

## Anonymisation par zones (pinceau) 🖌

Pour intégrer **n'importe quel** nouveau format de CR, on peut sélectionner
**visuellement** les zones à masquer, directement sur l'aperçu PDF :

1. **Gestion de document** → nom du type + clé API + **CR fictif** (faux patient).
2. **Analyser avec l'IA** : l'IA repère les champs et **pré-positionne des zones**.
3. **Au pinceau**, sur l'original (à gauche) : **dessine** un rectangle sur chaque
   info à masquer, **déplace** / **redimensionne** / **supprime** les zones, et
   choisis leur **catégorie** (Nom / N° / Naissance / Autre).
4. **👁 Aperçu** : voir le résultat anonymisé (à droite, en rouge).
5. **💾 Enregistrer** le type.

- **Hybride** : pour chaque zone, le texte qu'elle contient est masqué **partout**
  dans le document (en-têtes/pieds de page répétés) **et** le rectangle est noirci
  (couvre aussi les zones sans couche texte).
- **Multi-pages** : toutes les pages s'affichent ; dessine sur n'importe laquelle.
- **Pages tournées** gérées (ECG/écho exportés en rotation).
- Coordonnées **normalisées** : indépendantes du zoom et du format.

### Édition d'un type existant + PDF de référence

Chaque type personnalisé a un bouton **✏ Modifier** : ses **zones existantes** se
rechargent pour ajustement. Le **CR fictif** utilisé est **stocké** comme PDF de
référence → la ré-édition se fait **sans re-uploader** (et il est inclus dans la
sauvegarde de configuration). ⚠️ C'est bien un **CR fictif** qui est stocké.

---

## Le Workflow en pratique 🗂

1. Les dossiers à suivre sont cochés dans **⚙ Configuration** (bouton **📁
   Parcourir** pour choisir le dossier).
2. Dès qu'un examen (PDF) **arrive** dans un dossier surveillé, il apparaît en
   tête et est **anonymisé automatiquement** (✓). Le **liséré coloré** indique le
   type.
3. On le sélectionne : la liseuse affiche le document anonymisé (bascule
   Original / Anonymisé) + le récapitulatif des éléments masqués.
4. On saisit son **interprétation** (ex. *« hypokinésie basale inférieure, fuite
   mitrale modérée »*), puis **Générer le CR (IA)**.
5. Le CR généré s'affiche, est **enregistré en `.txt`** à côté du PDF source, et
   peut être exporté en **courrier Word** (gras).

**Détails importants :**
- Le listing est **instantané** (le scan tourne en tâche de fond, réutilisé tant
  que rien ne change — adapté à un partage réseau avec beaucoup de dossiers).
- Le Workflow ne prend en compte que les examens **arrivés après l'ouverture** du
  logiciel : l'antériorité (le stock existant) n'est **jamais** re-traitée.
- Le tri/affichage se fait sur l'**heure d'arrivée** (date de création locale),
  pas sur la date de modification — un appareil à l'horloge décalée ne fausse plus
  l'ordre chronologique.

---

## Génération de CR par IA 🧠

Configurable dans **⚙ Configuration → Génération de CR (IA)** :

- **Fournisseur** : DeepSeek, OpenAI ou Anthropic (Claude).
- **Clé API** (stockée localement dans `%LOCALAPPDATA%\MedAiCR\llm.json`), avec un
  bouton **🔌 Tester la connexion** (un test réussi enregistre la clé).
- **Modèle** modifiable, et **un system prompt par type d'examen** (l'équivalent
  d'un GEM Gemini / GPT dédié).

> ⚠️ La génération **envoie le texte anonymisé à un service externe**. Vérifie le
> récapitulatif des éléments masqués avant d'envoyer. La surveillance automatique
> de dossiers ne fait QUE l'anonymisation, **jamais** de génération IA.

### Courrier Word (gras) 📄

Le CR généré peut être téléchargé en **`.rtf`** (bouton **📄 Courrier**) : il
s'ouvre dans **Word / LibreOffice** avec les **valeurs importantes en gras**, et
reste **modifiable** (ajoute en-tête, patient, signature, puis « Enregistrer sous
.docx » si besoin). Le `.txt` reste, lui, en texte brut.

---

## Surveillance automatique de dossiers (watcher) 👀

En tâche de fond, dès qu'un PDF est déposé dans un dossier surveillé, une version
anonymisée est créée **dans le même dossier**, mise en page conservée, sous un
**nom neutre** `ANOM_0001.pdf`, `ANOM_0002.pdf`… (aucun nom patient dans le nom de
fichier). Récursif par défaut. **Seuls les nouveaux** fichiers sont traités (le
stock existant est ignoré à l'initialisation).

Réglages dans **`config.json`** (relu à chaud, éditable via l'onglet
Configuration) :

```json
{
  "poll_interval_seconds": 10,
  "watch": [
    { "directory": "C:/Users/.../CR_a_anonymiser/ETT",
      "cr_type": "echo_cardiaque", "enabled": true, "recursive": true }
  ]
}
```

- `cr_type` : `echo_cardiaque`, `polygraphie`, `holter`, un type personnalisé, ou
  **`auto`** (détection d'après le contenu).
- Garde-fous : fichiers `ANOM_*` ignorés, pas de retraitement, attente de
  stabilité du fichier, PDF scannés (sans texte) signalés et ignorés.

---

## Couleurs & sauvegarde

- **🎨 Couleurs par type** (⚙ Configuration) : choisis la couleur du liséré des
  cartes du Workflow pour repérer chaque type d'un coup d'œil.
- **💾 Sauvegarde / restauration** (⚙ Configuration) : exporte **toute** la
  configuration dans un seul fichier — dossiers surveillés, réglages IA + prompts
  système + clé API, types personnalisés **et leurs PDF de référence**. Pratique
  après une réinstallation.

---

## Installation (.exe, sans Python)

- **Installeur** : `installer/MedAiCR_Setup_1.2.0.exe` — installation par
  utilisateur, **sans droits administrateur**, raccourcis menu Démarrer + Bureau.
- **Version portable** : `dist/MedAiCR.exe` — se lance tel quel.

L'exécutable embarque tout (serveur web + interface + PDF.js + surveillance). Au
lancement, le navigateur s'ouvre sur l'interface ; **fermer la fenêtre** (console)
arrête l'application. Les données propres à chaque poste sont créées au 1er
lancement dans `%LOCALAPPDATA%\MedAiCR\` (config, clés, types, PDF de référence,
journaux). Une mise à jour de l'app **ne touche pas** à ces données.

**Reconstruire** : `build_installer.bat` (nécessite le venv +
[Inno Setup 6](https://jrsoftware.org/isinfo.php) pour l'installeur).

---

## Développement

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn main:app --app-dir backend --port 8000
# puis ouvrir http://127.0.0.1:8000
```

(ou double-clic sur `run.bat`, qui crée le venv au premier lancement ; ou
`Lancer_Anonymiseur.bat` pour serveur + surveillance + navigateur).

## Structure

```
app_launcher.py     point d'entrée de l'app empaquetée (serveur + watcher + navigateur)
watcher.py          surveillance de dossiers (anonymisation automatique)
backend/
  main.py           API FastAPI + service du front
  appconfig.py      chemins portables (dev vs .exe) + config par défaut
  pdf_extract.py    extraction texte (pdfplumber) + nettoyage des caractères parasites
  extractors.py     extraction des identifiants par type de CR (libellés)
  rules.py          règles regex génériques + couleurs/labels des types
  anonymizer.py     moteur texte (extraction → remplacement global → règles)
  zones.py          anonymisation par ZONES dessinées sur le PDF (pinceau)
  pdf_redact.py     rédaction du PDF (PyMuPDF) + zones + effacement des métadonnées
  custom_types.py   types appris via IA + stockage des PDF de référence
  worklist.py       Workflow : liste de travail + anonymisation en tâche de fond
  llm.py            génération de CR multi-fournisseurs (DeepSeek/OpenAI/Anthropic)
frontend/
  index.html        interface (Workflow / Manuel / Gestion de document / Configuration)
  vendor/pdfjs/     PDF.js (liseuse + dessin des zones, hors-ligne)
```

## Limites

- PDF **scannés** (images sans couche texte) non gérés pour l'extraction (pas
  d'OCR) — mais une **zone** dessinée les noircit quand même.
- Les règles natives sont calibrées sur les modèles fournis ; un nouveau format
  peut nécessiter un **type personnalisé** (libellés et/ou zones). Le
  récapitulatif et l'aperçu permettent de **vérifier avant** tout envoi à une IA.

## Avertissement (rappel)

**Outil à des fins pédagogiques uniquement — ce n'est PAS un dispositif médical
(DM).** Non certifié, non marqué CE, non destiné à un usage clinique ou
diagnostique, et à ne pas utiliser sur des patients réels.

La responsabilité de vérifier l'anonymisation (récapitulatif des éléments masqués
+ aperçu PDF) **avant** toute transmission à un service d'IA incombe à
l'utilisateur. Aucune garantie d'exhaustivité du masquage sur un format de
document non prévu, et aucune garantie d'exactitude des CR générés par l'IA.
