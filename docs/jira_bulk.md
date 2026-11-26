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

## 2. Instancier des tickets template

```
.\.venv\Scripts\python.exe manage.py jira_bulk instance "sprint = 123" --epic BKL-456 --sprint 789 --via "Canceled>Open>Backlog" --dry-run
```

Les templates sont les tickets du JQL (n'importe lequel), dans l'ordre de son `ORDER BY`, sinon
celui du backlog. Les sous-tâches que le JQL renverrait sont ignorées : elles sont clonées avec
leur parent. Un ticket déjà dans le sprint cible est ignoré (« already in the target sprint »).

Pour chaque ticket template, puis ses sous-tâches :

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

Niveau de sécurité : un ticket template restreint n'est **jamais** copié sans son niveau. Si le
niveau ne peut pas être posé sur la copie, le ticket (et ses sous-tâches) n'est pas cloné et
passe en erreur ; commentaires et pièces jointes ne sont copiés qu'après avoir vérifié que la
copie a le même niveau que son template.

Les rapports CSV neutralisent les cellules qui commencent par `=`, `+`, `-` ou `@` (pas de
formule exécutée à l'ouverture dans Excel).

Les ids de sprint se lisent dans l'URL du board (`sprint=123`) ou dans JiraFox.
