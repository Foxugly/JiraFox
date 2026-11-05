# Rapport direction : analyse IA d'un ou de plusieurs sprints, en PowerPoint

## Intention

Le dev manager présente à la direction l'avancement de l'équipe, ses risques et ses métriques Kanban,
sous forme de PowerPoint. Aujourd'hui, ce deck se monte à la main à partir des pages de JiraFox.
JiraFox doit produire ce PowerPoint lui-même, pour un sprint ou pour plusieurs. Les chiffres et les
graphiques sont calculés par JiraFox, et une IA rédige le texte d'analyse (synthèse et risques).

Renaud génère le rapport sur son PC, avec JiraFox qui reste un outil **local**, puis l'envoie au dev
manager. Le dev manager ne se sert jamais de JiraFox.

## Objectifs

1. **Rapport d'un sprint** : depuis la page d'un sprint, un bouton « Rapport direction » produit un
   `.pptx`.
2. **Vue sur plusieurs sprints** : une nouvelle page affiche, pour une sélection de sprints, les
   tendances de flux, la prévisibilité et l'instantané Kanban. Elle propose le même bouton.
3. **IA interchangeable** : un mécanisme de « fournisseurs d'analyse ». La v1 en livre deux :
   **Copilot manuel** et **sans IA**. D'autres fournisseurs (API compatible OpenAI ou Azure OpenAI,
   Claude…) se brancheront plus tard sans toucher au reste.

## Hors objectifs (v1)

- Aucune **vélocité**, ni en story points ni en tickets : Renaud la refuse comme métrique. Le flux
  Kanban la remplace.
- Pas de version néerlandaise : le français suffit pour commencer.
- Pas d'appel API à une IA : seulement le mode manuel Copilot.
- Pas d'envoi du rapport (mail, partage) : Renaud télécharge le fichier et l'envoie lui-même.
- Pas d'hébergement de JiraFox ni d'accès pour le dev manager.

## Anti-objectifs (ce qui ferait échouer le projet même si ça marche)

- **Un chiffre écrit par l'IA qui ne vient pas de JiraFox.** L'IA commente des chiffres fournis.
  Les chiffres des slides sont toujours ceux calculés par JiraFox, jamais ceux qu'elle écrit dans
  son texte.
- **Des données envoyées à l'IA sans que Renaud les ait vues.** Renaud veut une analyse aussi riche
  que possible (décision du 05/11/2026) : le dossier est complet, mais il est toujours affiché en
  entier avant d'être copié. La v1 ne fait aucun envoi automatique.
- **Le modèle PowerPoint Solidaris commité dans le dépôt GitHub**, qui est public.
- **Un rapport qui casse quand Jira est incomplet** (historique manquant, sprint sans tickets) :
  le rapport sort quand même et le dit.

## Contraintes

- JiraFox est un outil local (Django 6, Python 3.14, client `jira` 3.10). Pas de CI, pas de
  déploiement.
- Les métriques réutilisent ce qui existe : `sprint/scope_flow.py` (périmètre du sprint, taux,
  garde-fous `consistency_errors`) et `jiramodule/services/kanban_metrics.py` (temps de cycle,
  temps de traversée, âge du WIP, en jours ouvrés).
- Interface en FR, NL et EN comme le reste de JiraFox (`{% trans %}`, fichiers `.po`). Le contenu du
  PowerPoint, lui, est en français.

## Ce qui a été décidé

### Le deck

Le modèle est construit à partir du PowerPoint de l'entreprise, chargé dans JiraFox (voir plus bas).
Ce modèle compte 6 slides d'exemple : titre, layout 1 (2 colonnes), layout 2 (blocs empilés),
layout 3 (texte et panneau rouge), layout 4 (3 lignes), « Merci ». Le placement validé :

| # | Slide | Modèle | Contenu |
|---|---|---|---|
| 1 | Titre | Titre | « Rapport d'équipe », nom du projet JiraFox et période (sprint ou plage), « Direction • Date » |
| 2 | Synthèse (IA) | Layout 2 | Bloc « Où en est l'équipe » + bloc « Tendance » (5 à 7 puces au total) |
| 3 | Avancement | Layout 3 | Graphique prévus / ajoutés / clôturés / non commencés ; chiffre clé dans le panneau rouge (« 59 % des prévus clôturés ») |
| 4 | Flux sur plusieurs sprints | Titre + graphique | Débit (tickets clôturés par sprint), temps de cycle médian et 85ᵉ percentile, WIP en fin de sprint |
| 5 | Prévisibilité | Titre + graphique | Par sprint : % des prévus clôturés et % de tickets ajoutés en cours de sprint |
| 6 | Instantané Kanban | Layout 1 | « WIP et vieillissement » / « Les plus anciens » (tickets en cours les plus âgés) |
| 7 | Risques (IA) | Layout 4 | 3 risques maximum, chacun avec un titre et une explication |
| 8 | Annexe | Titre + tableau | Tickets clôturés et non terminés (clé, titre, statut de fin) |
| 9 | Fin | Merci | — |

- **Graphiques PowerPoint natifs et modifiables**, pas des images.
- Le texte de l'IA porte la mention « Texte généré par IA à partir des métriques JiraFox — à relire
  avant diffusion », à retirer à la main.
- **Rapport d'un seul sprint** : les slides 4 et 5 montrent ce sprint et les 5 sprints fermés
  précédents du board, pour donner une tendance.
- **Sans IA** : les slides 2 et 7 sont omises, les autres sortent normalement.

### Les métriques

- **Avancement, prévisibilité** : celles du périmètre du sprint (`build_scope_flow`). Les tickets
  terminés en dehors du sprint sont exclus, les statuts du réglage « comptés comme commencés » sont
  pris en compte, et `consistency_errors` doit être vide. Sinon, le rapport le signale (voir les
  garde-fous).
- **Débit d'un sprint** : nombre de tickets clôturés dans le sprint (prévus et ajoutés).
- **Temps de cycle** : `cycle_time_days` des tickets clôturés dans le sprint, en médiane et
  85ᵉ percentile.
- **WIP en fin de sprint** : tickets du sprint non terminés à sa fin.
- **Instantané Kanban** : au dernier jour de la période, WIP, part des tickets vieillissants (même
  seuil que le tableau de bord, `AGING_WIP_THRESHOLD_DAYS`) et les 5 tickets en cours les plus âgés.

### La vue sur plusieurs sprints

- Nouvelle page, accessible depuis le menu « Sprint ».
- **Deux façons de sélectionner**, au choix :
  - une **plage de dates** : tous les sprints du board dont la période recoupe la plage ;
  - une **liste de sprints** cochés un par un.
- La page affiche :
  - les tendances (graphiques des slides 4 et 5) ;
  - l'instantané Kanban ;
  - un tableau récapitulatif d'une ligne par sprint ;
  - les alertes de garde-fous de chaque sprint ;
  - le bouton « Rapport direction ».

### Les fournisseurs d'analyse (IA)

- Un fournisseur reçoit un **dossier d'analyse** et rend une **analyse** : synthèse « Où en est
  l'équipe », synthèse « Tendance », jusqu'à 3 risques (titre et explication). C'est l'interface
  commune que tout futur fournisseur (API) implémentera.
- **Dossier d'analyse** : le maximum utile, au choix de Renaud (05/11/2026).
  - Les métriques agrégées par sprint (périmètre, flux, prévisibilité, Kanban).
  - Pour chaque ticket du périmètre : clé, type, titre, assigné, statut au début, statut à la fin,
    origine (prévu / ajouté), issue (clôturé / non terminé / retiré / terminé ailleurs), âge dans son
    statut, et s'il est revenu en arrière.
  - Pour les tickets à risque (les plus anciens en cours, les non commencés, ceux revenus en arrière) :
    en plus, le début de la description (500 caractères au plus).
  - Pas les commentaires : trop volumineux pour un copier-coller, et peu utiles aux tendances.
  - Le dossier est affiché en entier avant le bouton « Copier » ; c'est ce texte exact qui part.
- **Copilot manuel** :
  1. JiraFox affiche la consigne et le dossier, avec un bouton « Copier ».
  2. Renaud les colle dans Copilot.
  3. Il recolle la réponse de Copilot dans un champ de JiraFox.
  4. JiraFox lit la réponse et montre la synthèse et les risques, que Renaud peut corriger avant de
     générer le PowerPoint.

  La consigne demande une réponse dans un format fixe, facile à relire et à analyser (par exemple
  des titres de section imposés).
- **Sans IA** : pas de consigne. Le PowerPoint sort sans les slides 2 et 7.
- Le fournisseur se choisit au moment de générer le rapport. La v1 n'a besoin d'aucun réglage, ni
  de clé, ni d'URL.

### Le modèle PowerPoint

- Un champ « Modèle PowerPoint » dans les réglages du projet permet d'envoyer un `.pptx`. Le fichier
  est stocké dans un dossier local ignoré par git.
- Le générateur part du modèle : il réutilise ses slides d'exemple comme gabarits, remplace leurs
  textes, ajoute les graphiques et supprime les slides inutilisées.
- Sans modèle chargé, JiraFox utilise une mise en page neutre (fond blanc, couleurs de JiraFox).

### Garde-fous

- Les alertes de la page « Périmètre du sprint » (chiffres incohérents, tickets absents du rapport
  Jira, sans historique, terminés ailleurs, prévus créés après le début) sont reprises :
  - sur la page « Plusieurs sprints », pour chaque sprint ;
  - dans le PowerPoint, dans les **notes du présentateur** de la slide concernée, pas sur la slide.
- Un sprint dont les chiffres ne tombent pas juste (`consistency_errors`) reste dans le rapport,
  avec une alerte rouge sur la page avant la génération.

### Définitions précises (une seule définition partout)

- **« Commencé »** : le statut appartient à la catégorie Jira « En cours » ou « Terminé », ou fait
  partie des « statuts comptés comme commencés » du projet. C'est la définition du périmètre du sprint
  (`completion_rates`). Le temps de cycle du rapport l'utilise aussi : son horloge démarre au premier
  passage dans un statut « commencé ». L'heuristique actuelle de `kanban_metrics`, qui cherche
  « progress » dans le nom du statut, n'est pas reprise.
- **« Clôturé dans le sprint »** : la règle de `completion_rates` : résultat Jira « Terminés », ou
  statut de catégorie « Terminé » à la fin du sprint. Le débit, le temps de cycle et les % clôturés
  utilisent tous cette règle.
- **Slide 3, chiffre clé** : `completion["planned"]["percent_total"]`, soit les prévus clôturés sur
  le total des prévus.
- **Slide 5** :
  - « % des prévus clôturés » se calcule comme au point précédent ;
  - « % ajoutés » = tickets ajoutés / (prévus + ajoutés).
  - Si le dénominateur vaut 0, le graphique laisse un trou (pas de 0), et le tableau affiche « - ».
- **Instantané Kanban** : il est reconstruit **à la date de fin de la période** à partir de
  l'historique (`status_at`), même pour un sprint fermé il y a longtemps. Il porte sur les tickets non
  terminés du dernier sprint de la période. Leur âge se mesure depuis leur premier statut
  « commencé » jusqu'à cette date de fin.
- **Sprints pris en compte** :
  - **Tendances** : seulement les sprints **fermés** du board du projet. Les « 5 précédents » sont
    les 5 derniers par date de fin, ou moins s'il en existe moins.
  - **Rapport d'un seul sprint** : possible aussi sur le sprint actif, avec le titre marqué
    « (en cours) ».
  - **Plage de dates** : elle ignore les sprints actifs et futurs.

### Déroulé de la génération

- La réponse de Copilot, une fois analysée et corrigée par Renaud, est gardée dans la **session**
  Django jusqu'à la génération. Aucun nouveau modèle en base n'est nécessaire.
- Une réponse mal formée affiche un message clair. Renaud peut alors la corriger dans les champs, ou
  générer le rapport « sans IA ».
- Le modèle PowerPoint est un `FileField` du projet, stocké sous `MEDIA_ROOT`, et ce dossier est
  ajouté au `.gitignore`. Un modèle qui n'a pas les 6 slides d'exemple attendues est refusé au
  chargement, avec un message qui l'explique. La génération utilise alors la mise en page neutre.

## Ce qui est laissé au constructeur

- La bibliothèque de génération PowerPoint (`python-pptx` est le choix évident) et la façon de
  cloner les slides d'exemple du modèle.
- Le format exact de la réponse attendue de Copilot et la tolérance de l'analyse (titres, puces),
  pourvu qu'une réponse mal formée produise un message clair et laisse corriger à la main.
- Le cache des données Jira par sprint, pour que la vue sur plusieurs sprints reste rapide.
- La forme exacte des graphiques à l'écran (Chart.js, comme le reste de JiraFox).

## Choix faits par Claude (à vérifier)

- Tendance de 5 sprints fermés précédents pour le rapport d'un seul sprint.
- WIP mesuré en fin de sprint, et non en moyenne pendant le sprint.
- Annexe limitée aux tickets clôturés et non terminés, sans les assignés (le deck circule en
  direction ; les assignés vont à l'IA, pas sur les slides).
- « Un maximum » interprété comme : titres, assignés et début de description des tickets à risque,
  mais pas les commentaires ; la description est tronquée à 500 caractères.
- Avertissements dans les notes du présentateur plutôt que sur les slides.
- Une seule définition de « commencé » et de « clôturé », celle du périmètre du sprint, y compris
  pour le temps de cycle du rapport.
- Instantané Kanban reconstruit à la date de fin de la période, pas à aujourd'hui.
- Tendances limitées aux sprints fermés ; le rapport d'un seul sprint peut porter sur le sprint actif.
- Analyse gardée en session ; modèle PowerPoint en `FileField` sous `MEDIA_ROOT`, ignoré par git.
