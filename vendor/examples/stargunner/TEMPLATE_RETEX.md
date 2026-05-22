# Shmup StarGunner -> Retours utiles pour le template 2026

Ce document liste ce que le developpement de `Shmup_StarGunner` a mis en evidence pour faire evoluer le template de base NGPC 2026.

L'objectif n'est pas de remonter toute la logique du jeu dans le template, mais d'identifier:
- les modules generiques qui manquent,
- les modules existants qui meritent d'etre renforces,
- les patterns de code qui devraient devenir des bases officielles du template,
- et ce qui doit au contraire rester specifique au shmup.

## 1. Constats generaux

Le projet a depasse le simple stade de prototype graphique:
- plusieurs ecrans etats (`intro`, `menu`, `options`, `high scores`, `gameplay`, `stage clear`, `continue`, `game over`, `name entry`),
- un vrai flow de partie,
- de la persistence flash,
- un usage concret du DMA raster,
- un budget sprites/palettes serre,
- une logique de progression arcade.

En pratique, cela a revele que le template de base est deja bon sur le noyau technique, mais qu'il manque encore plusieurs briques "jeu complet".

## 2. Nouveaux modules a ajouter au template

### 2.1 Module `profile/save`

But:
- encapsuler la sauvegarde persistante d'un jeu dans une structure versionnee et verifiee.

Ce que le module devrait apporter:
- structure de save avec `magic`, `version`, `checksum`,
- defaults propres si save absente ou corrompue,
- lecture/ecriture robustes via `ngpc_flash`,
- helpers simples pour options persistantes,
- place prevue pour top 10, flags, options, progression.

Pourquoi:
- `ngpc_flash` brut est utile, mais insuffisant seul pour un vrai jeu,
- presque tous les jeux finissent par avoir besoin d'un petit "profile layer" au-dessus.

### 2.2 Module `high score table`

But:
- proposer un top 10 generique reutilisable.

Fonctions attendues:
- insertion triee,
- lecture d'une entree,
- test "est-ce que ce score qualifie",
- format standard `name + score`,
- taille configurable si besoin.

Pourquoi:
- c'est une brique arcade classique,
- elle est suffisamment generique pour ne pas etre specifique au shmup.

### 2.3 Module `name entry`

But:
- offrir une saisie pad pour initiales / pseudo court.

Fonctions attendues:
- saisie 3 caracteres minimum,
- curseur gauche/droite,
- changement caractere haut/bas,
- alphabet configurable,
- case `OK`.

Pourquoi:
- tres frequent en jeu arcade,
- penible a recoder proprement a chaque projet.

### 2.4 Module `text scene / end screen`

But:
- eviter de reprogrammer a la main chaque ecran systeme simple.

Fonctions attendues:
- preparation ecran noir,
- palette texte definie une fois,
- helpers titre / lignes / prompt,
- choix `YES / NO`,
- ecrans simples de type:
  - `stage clear`,
  - `continue`,
  - `game over`,
  - `credits`,
  - `save complete`.

Pourquoi:
- tres reutilisable,
- utile bien au-dela du shmup.

### 2.5 Module `power meter`

But:
- fournir un socle de "barre / roue / meter de power-up" pour jeux arcade.

Fonctions attendues:
- slots definis par table,
- niveau max par slot,
- activation,
- feedback `maxed/full`,
- regles de consommation simples.

Pourquoi:
- plus specifique arcade/shmup,
- mais suffisamment general si le template vise aussi ce type de jeux.

### 2.6 Module `stage script`

But:
- proposer un lecteur de script d'evenements de niveau.

Fonctions attendues:
- `wave`,
- `wait clear`,
- `pickup`,
- `text`,
- `change speed`,
- `boss trigger`,
- eventuellement sections / acts.

Pourquoi:
- le pattern s'est revele tres efficace,
- il peut servir a d'autres jeux d'action/arcade, pas seulement a ce shmup.

## 3. Modules existants a faire evoluer

### 3.1 `ngpc_flash`

Etat actuel:
- bon backend brut de persistence.

Ce qu'il faudrait ajouter ou mieux documenter:
- insister sur le fait qu'il faut une surcouche applicative versionnee,
- documenter le pattern `magic + version + checksum + defaults`,
- fournir eventuellement un helper de validation de bloc.

Pourquoi:
- le backend seul ne suffit pas pour faire une save robuste.

### 3.2 `ngpc_text`

Etat actuel:
- suffisant pour l'affichage texte de base.

Ce qu'il faudrait ajouter:
- helpers de mise en page simples,
- ecrans texte "systeme",
- utilitaires de centrage leger ou de blocs.

Important:
- ne pas promettre la transparence si l'implementation ne le permet pas.

Pourquoi:
- les jeux recodent sinon toujours la meme plomberie UI.

### 3.3 `ngpc_dma` / `ngpc_dma_raster`

Etat actuel:
- le socle valide est maintenant bon,
- mais les usages reels doivent etre mieux cadrees.

Ce qu'il faut renforcer:
- documentation claire sur:
  - `manual rearm` vs `auto-rearm`,
  - cas de test vs cas gameplay reel,
  - configuration recommandees,
  - pitfalls d'integration.
- exemples concrets:
  - raster XY u16,
  - wobble de fond,
  - integration VBlank propre.

Pourquoi:
- on a constate qu'un mode tres bon en homebrew de test ne se transpose pas automatiquement tel quel dans un jeu complet.

### 3.4 `ngpc_sprite`

Etat actuel:
- API correcte, mais certaines subtilites sont piegeuses.

Ce qu'il faut mieux documenter:
- difference entre:
  - `hide`,
  - `move`,
  - `set`,
  - `set_tile`,
  - `set_flags`.

Point critique rencontre:
- `move()` ne restaure pas un sprite masque via `hide()`,
- cela a provoque un boss "present mais invisible".

Pourquoi:
- c'est un vrai piege de production.

### 3.5 Audio / driver son / mapping SFX-BGM

Etat actuel:
- le driver est capable de restaurer les voix,
- mais le routage des SFX reste un sujet de design.

Ce qu'il faut mieux cadrer:
- bonnes pratiques de partage de canaux PSG,
- priorites / restitution,
- consequences d'un SFX tonal sur une voix deja occupee par la BGM,
- conseils d'export ou de mapping.

Pourquoi:
- sujet recurrent des qu'on a une vraie musique de menu + UI + gameplay.

### 3.6 Pattern de state machine principale

Etat actuel:
- beaucoup d'exemples de template vivent avec des retours simples de type bool.

Ce qu'il faudrait faire evoluer:
- encourager des retours par `enum` ou `state result`,
- formaliser les transitions:
  - retour menu,
  - retry,
  - continue,
  - niveau suivant,
  - game over final.

Pourquoi:
- le simple `0/1` devient vite trop limite pour un vrai jeu multi-ecrans.

## 4. Patterns a officialiser dans le template

### 4.1 Separation nette entre "socle moteur" et "flow de jeu"

Le projet montre qu'il faut distinguer:
- les modules techniques reutilisables,
- et les etats de jeu specifiques a chaque production.

Le template devrait fournir:
- des patterns clairs,
- pas uniquement des fonctions brutes.

### 4.2 Gestion du budget sprite/palette

Sujet majeur rencontre:
- conflits entre HUD, start banner, pickups, boss room, etc.

Ce qu'il faudrait ajouter au template:
- recommandations officielles de layout sprite slots,
- recommandations de budget palettes,
- exemple d'allocation fixe par familles de sprites,
- documentation des zones "mutualisables".

Pourquoi:
- une grosse partie des regressions visuelles vient de la.

### 4.3 Scenes texte separables du gameplay

Pattern valide:
- en fin de boss ou game over, basculer sur une scene texte propre,
- cacher sprites gameplay,
- couper/adapter musique,
- afficher un ecran simple et stable.

Le template gagnerait a fournir ce pattern explicitement.

### 4.4 Persistence tres tardive et peu frequente

Pattern retenu:
- ne pas sauver en permanence,
- sauver quand une option change,
- sauver quand un score est valide.

Ce principe devrait etre documente dans le template autour de `ngpc_flash`.

## 5. Ce qui doit rester specifique au jeu

Il ne faut pas tout remonter.

Doivent rester dans le shmup:
- les valeurs precises d'equilibrage,
- l'ordre exact des power-ups,
- les patterns de boss propres a StarGunner,
- les scripts de niveau,
- la logique specifique `Level 1 -> Level 2`,
- la difficulte,
- le rythme des capsules,
- les choix d'UI propres au jeu.

Le template doit fournir le cadre, pas imposer le game design.

## 6. Priorites recommandees pour le template

### Priorite haute

- integrer un exemple officiel `save/profile` sur `ngpc_flash`,
- integrer un mini `high score table`,
- integrer un `name entry` 3 caracteres,
- renforcer la doc `DMA raster`,
- renforcer la doc `sprite hide/move/set`,
- proposer un pattern de `state flow` plus riche que le simple bool.

### Priorite moyenne

- helper `text scene / end screen`,
- helper ou module `power meter`,
- exemple officiel de layout HUD/sprite slots,
- doc audio sur coexistence BGM/SFX.

### Priorite basse

- generaliser le `stage script`,
- proposer un "boss room flow" type,
- proposer des presets arcade plus haut niveau.

## 7. Conclusion

Le shmup a surtout montre trois choses:

1. Le template 2026 est deja bon comme base technique.
2. Ce qui manque maintenant, ce sont surtout des briques de "jeu complet".
3. La documentation doit monter en niveau sur les zones qui coutent du temps en production:
   - DMA reel,
   - sprites,
   - audio,
   - save,
   - state flow.

En bref:
- peu de revolutions cote noyau,
- mais plusieurs modules generiques et plusieurs clarifications API/doc a ajouter pour transformer le template en vraie base de production arcade.
