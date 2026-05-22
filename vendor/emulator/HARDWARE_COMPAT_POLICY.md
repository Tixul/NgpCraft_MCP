# NgpCraft Emulator - Hardware Compatibility Policy

## 1. But

Le projet ne vise pas une machine "idealisee".
Il vise une machine utile, mais fidele au comportement du hardware reel, y compris quand ce comportement est moche.

En clair:
- si le hardware reel boote, l'emulateur doit booter
- si le hardware reel glitch, l'emulateur doit glitcher
- si le hardware reel freeze ou plante sur un cas connu, l'emulateur de reference doit freeze ou planter aussi

La valeur ajoutee de l'emulateur n'est pas de cacher ces problemes.
La valeur ajoutee est de les expliquer.

## 2. Regle cardinale

Le coeur d'emulation n'a pas le droit de "corriger" en douce:
- un opcode casse
- un comportement non documente mais observe
- un bug silicium
- un timing limite qui casse sur machine reelle

Le mode de reference doit rester hardware-faithful.

## 3. Deux couches distinctes

### 3.1 Couche execution

Responsable de:
- reproduire le comportement reel
- y compris les comportements defectueux connus

Cette couche decide:
- ce qui est execute
- comment c'est execute
- quand ca plante

### 3.2 Couche diagnostic

Responsable de:
- observer
- annoter
- expliquer
- capturer

Cette couche peut:
- signaler qu'un opcode casse vient d'etre execute
- signaler qu'un pattern correspond a un bug silicium connu
- produire un rapport de crash enrichi
- suggerer une piste

Cette couche ne peut pas:
- changer l'execution par defaut
- contourner un freeze
- corriger un registre ou un flag
- inventer un chemin "plus stable" que le hardware reel

## 4. Modes autorises

### 4.1 Reference hardware

Mode par defaut pour:
- validation
- regression
- comparaisons hardware
- debug serieux

Proprietes:
- comportement de reference
- quirks actifs
- aucune correction silencieuse

### 4.2 Diagnostic assist

Meme execution que `reference hardware`, avec en plus:
- overlays
- warnings
- etiquettes de quirk
- crash reports plus riches
- exports de timeline/trace

Important:
- les diagnostics n'ont pas le droit de changer le resultat

### 4.3 Non-reference modes

Si un jour un mode plus permissif existe pour le confort utilisateur:
- il doit etre optionnel
- il doit etre clairement etiquete non-reference
- il ne doit jamais servir de base pour valider la toolchain
- il ne doit jamais remplacer le mode de reference dans les tests

## 5. Base de connaissances quirk

Le projet doit maintenir une base versionnee des cas connus:
- opcodes casses
- instructions partiellement documentees
- timings limites
- comportements DMA/IRQ/video atypiques
- bugs silicium confirmes ou fortement suspectes

Pour chaque entree:
- identifiant unique
- categorie
- description courte
- source documentaire ou observation
- niveau de confiance
- ROM ou test de reproduction
- impact visible
- statut implementation

Niveaux de confiance recommandes:
- `documented`
- `observed`
- `suspected`

## 6. Politique de crash

Quand le hardware reel crash sur un cas connu, l'emulateur doit:
- reproduire le crash ou freeze dans le mode de reference
- capturer les dernieres instructions
- capturer l'etat CPU
- capturer les derniers evenements machine importants
- fournir un resume lisible

Le rapport ideal contient:
- PC, SP, flags, registres
- derniere instruction executee
- 32 a 256 dernieres instructions selon le mode
- derniers evenements IRQ/DMA/HBlank/VBlank
- acces memoire/IO recents si actifs
- quirk ou bug connu potentiellement implique
- lien vers la doc ou le test associe si disponible

## 7. Politique d'undefined behavior

Quand un comportement est reellement inconnu:
- ne pas inventer un comportement "gentil"
- marquer le cas comme gap
- conserver une trace exploitable
- permettre d'ajouter rapidement un test de reproduction

Si un comportement probable existe mais n'est pas prouve:
- l'annoter comme `suspected`
- le garder desactive par defaut tant qu'il n'est pas valide
- ne jamais le presenter comme "hardware accurate" sans preuve

## 8. Tests obligatoires

Chaque quirk important doit avoir, si possible:
- un test unitaire ou micro test
- une ROM de reproduction
- un attendu minimal
- un lien vers la source de verite

Familles prioritaires:
- opcodes casses
- prologues/stack edge cases
- DMA et timings video
- IRQ et priorites
- comportements de freeze observes sur hardware reel

## 9. Ce que le projet ne doit pas faire

Non acceptable:
- cacher un plantage avec un fallback silencieux
- continuer l'execution apres un etat impossible en pretendant que tout va bien
- afficher "supporte" pour un quirk non teste
- utiliser un mode permissif comme base du CI

## 10. Definition de succes

La politique est respectee quand:
- les cas de crash hardware connus sont reproduits
- le debugger explique mieux qu'un emulateur standard pourquoi ca casse
- les diagnostics aident, sans changer l'execution
- la base quirk devient un actif central du projet
