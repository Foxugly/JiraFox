# jira_bulk : changements en masse sur Jira

Commande Django, lancée depuis le dossier JiraFox. Elle utilise le projet courant et le token
Jira de l'utilisateur JiraFox (`--user <login>` s'il y en a plusieurs, à placer **avant** l'action).

Toujours commencer par `--dry-run` : rien n'est modifié, la commande montre ce qu'elle ferait.
Sans `--dry-run`, elle affiche le résumé et attend `oui`. Chaque exécution réelle écrit son
rapport dans `logs/jira_bulk/` (CSV, séparateur `;`).

## 1. Passer des tickets dans un statut

```
.\.venv\Scripts\python.exe manage.py jira_bulk transition "sprint = 123" --from "To Do" --to "Ready for sprint" --via "Canceled>Open>Backlog" --dry-run
```

- `--from` : seuls les tickets dans ce statut.
- `--via` : les statuts intermédiaires, dans l'ordre. Jira ne donne que les transitions possibles
  depuis le statut courant, d'où ce chemin explicite.
- Un ticket déjà à mi-chemin (exécution interrompue) reprend après l'étape où il est.
- Le dry-run ne peut vérifier que la première étape (les suivantes dépendent d'elle).
- Si `Canceled` pose une résolution qui reste après le retour en `Ready for sprint`, la ligne
  du ticket l'indique (`ATTENTION résolution encore posée`).
- Jira Server notifie à chaque transition faite par l'API (pas d'option pour l'éviter).

## 2. Instancier un sprint template

```
.\.venv\Scripts\python.exe manage.py jira_bulk instance 123 --epic BKL-456 --sprint 789 --via "Canceled>Open>Backlog" --dry-run
```

Pour chaque ticket du sprint template `123` (ordre du backlog), puis ses sous-tâches :

- crée une copie avec tous les champs de l'écran de création (résumé, description, priorité,
  composants, labels, versions, assignee, reporter, story points, estimation d'origine,
  échéance, champs personnalisés), puis les commentaires (auteur et date en tête), les pièces
  jointes et les watchers ;
- la copie est dans le sprint `789` **uniquement** (jamais dans le sprint template : vérifié
  après création) et dans l'epic `BKL-456` ; les sous-tâches suivent leur nouveau parent ;
- lien « clones » de la copie vers son ticket template ;
- la copie est amenée au **même statut** que son template, en suivant `--via` ;
- relancer la commande ne recrée rien : un template qui a déjà sa copie dans le sprint `789`
  est ignoré (« already cloned »).

Un champ refusé par Jira à la création (ex. reporter sans la permission) est retiré, la copie
est créée sans lui et la ligne du ticket le signale.

Les ids de sprint se lisent dans l'URL du board (`sprint=123`) ou dans JiraFox.
