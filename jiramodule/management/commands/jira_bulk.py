"""
Bulk changes on Jira, from the command line.

    manage.py jira_bulk transition "<jql>" --from "To Do" --to "Ready for sprint" --via "Canceled>Open>Backlog" --dry-run
    manage.py jira_bulk instance <template_sprint_id> --epic <epic key|id> --sprint <new_sprint_id> --via "Canceled>Open>Backlog" --dry-run

Without --dry-run the command shows what it is about to do and waits for "oui".
Each real run writes its report to logs/jira_bulk/.
"""

import csv
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from jiramodule.exceptions import JiraNotConfigured
from jiramodule.selectors import get_current_jira_context_for_user
from jiramodule.services.bulk import InstanceCloner, parse_path, search_all, walk_to_status
from jiramodule.services.jira_client import JiraManager
from jiramodule.services.jql import quote_value


class Command(BaseCommand):
    help = "Bulk Jira changes: walk issues to a status, or instance a template sprint into another sprint + epic."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="JiraFox login whose current project and Jira token are used.")
        sub = parser.add_subparsers(dest="action", required=True)

        transition = sub.add_parser("transition", help="Move the issues of a JQL to a status.")
        transition.add_argument("jql")
        transition.add_argument("--from", dest="from_status", help="Only the issues in this status.")
        transition.add_argument("--to", dest="to_status", required=True)
        transition.add_argument("--via", help='Intermediate statuses, in order: "Canceled>Open>Backlog".')
        transition.add_argument("--dry-run", action="store_true")
        transition.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

        instance = sub.add_parser("instance", help="Copy every issue of a template sprint into another sprint + epic.")
        instance.add_argument("sprint_id", type=int, help="Template sprint.")
        instance.add_argument("--epic", required=True, help="Epic of the copies (key or id).")
        instance.add_argument("--sprint", dest="new_sprint_id", type=int, required=True, help="Sprint of the copies.")
        instance.add_argument("--via", help="Statuses a new issue goes through to reach its template's status.")
        instance.add_argument("--dry-run", action="store_true")
        instance.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    # -- plumbing ----------------------------------------------------------------------------

    def _jira(self, username):
        User = get_user_model()
        users = User.objects.filter(jira_configs__isnull=False).distinct()
        if username:
            users = users.filter(username=username)
        if users.count() != 1:
            raise CommandError("Name the JiraFox user with --user." if not username else f"No user {username} with a Jira connection.")
        try:
            context = get_current_jira_context_for_user(users.get())
        except JiraNotConfigured as exc:
            raise CommandError(str(exc)) from exc
        manager = JiraManager(context)
        manager.connect()
        self.stdout.write(f"Jira: {context.jira_url} (project {context.project.name})")
        return manager.jira

    def _confirm(self, options, question):
        if options["dry_run"] or options["yes"]:
            return
        answer = input(f"{question} Tapez oui pour continuer : ").strip().casefold()
        if answer != "oui":
            raise CommandError("Abandon, rien n'a été modifié.")

    def _report(self, name, header, rows):
        folder = Path(settings.BASE_DIR) / "logs" / "jira_bulk"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{name}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(header)
            writer.writerows(rows)
        self.stdout.write(f"Rapport : {path}")

    def handle(self, *args, **options):
        jira = self._jira(options.get("user"))
        if options["action"] == "transition":
            self._transition(jira, options)
        else:
            self._instance(jira, options)

    # -- transition --------------------------------------------------------------------------

    def _transition(self, jira, options):
        jql = options["jql"]
        if options["from_status"]:
            jql = f"({jql}) AND status = {quote_value(options['from_status'])}"
        steps = parse_path(options["via"], options["to_status"])
        issues = search_all(jira, jql, fields="summary,status")
        self.stdout.write(f"JQL : {jql}\n{len(issues)} ticket(s), chemin : {' > '.join(steps)}")
        if not issues:
            return
        self._confirm(options, f"{len(issues)} ticket(s) vont changer de statut.")
        rows, failed = [], 0
        for issue in issues:
            current = ((issue.raw.get("fields") or {}).get("status") or {}).get("name") or ""
            result = walk_to_status(jira, issue.key, current, steps, dry_run=options["dry_run"])
            line = f"{issue.key}: {result.start} -> {result.end}"
            if result.error:
                failed += 1
                line += f"  ERREUR {result.error}"
            elif result.resolution:
                line += f"  ATTENTION résolution encore posée : {result.resolution}"
            self.stdout.write(line)
            rows.append([issue.key, result.start, result.end, " > ".join(result.done), result.resolution, result.error])
        mode = "simulation" if options["dry_run"] else "fait"
        self.stdout.write(f"{len(issues) - failed} OK, {failed} en erreur ({mode}).")
        if not options["dry_run"]:
            self._report("transition", ["ticket", "statut avant", "statut après", "étapes", "résolution", "erreur"], rows)

    # -- instance ----------------------------------------------------------------------------

    def _instance(self, jira, options):
        cloner = InstanceCloner(
            jira,
            sprint_id=options["sprint_id"],
            new_sprint_id=options["new_sprint_id"],
            epic=options["epic"],
            via=options["via"],
        )
        if options["sprint_id"] == options["new_sprint_id"]:
            raise CommandError("Le sprint des copies doit être différent du sprint template.")
        epic_key = cloner.resolve_epic()
        originals = cloner.originals()
        self.stdout.write(
            f"Sprint template {options['sprint_id']} : {len(originals)} ticket(s) -> sprint {options['new_sprint_id']}, epic {epic_key}"
        )
        if not originals:
            return
        self._confirm(options, f"{len(originals)} ticket(s) (+ sous-tâches) vont être clonés.")
        rows = []

        def show(result):
            who = f"  {result.original} (sous-tâche de {result.parent_clone})" if result.parent_clone else result.original
            if result.skipped:
                line = f"{who}: {result.skipped} -> {result.clone}"
            elif result.error:
                line = f"{who}: ERREUR {result.error}"
            else:
                target = result.status.end if result.status else ""
                line = f"{who} -> {result.clone or '(nouveau)'}  statut {target}"
            if result.dropped_fields:
                line += f"  champs non copiés : {', '.join(result.dropped_fields)}"
            for warning in result.warnings:
                line += f"\n    ! {warning}"
            self.stdout.write(line)
            rows.append(
                [
                    result.original,
                    result.clone,
                    result.parent_clone,
                    result.skipped,
                    result.status.end if result.status else "",
                    ", ".join(result.dropped_fields),
                    " | ".join(result.warnings),
                    result.error,
                ]
            )

        results = cloner.run(dry_run=options["dry_run"], on_result=show)
        errors = sum(1 for r in results if r.error)
        created = sum(1 for r in results if r.clone and not r.skipped)
        mode = "simulation" if options["dry_run"] else "fait"
        self.stdout.write(f"{len(results)} ticket(s) : {created} créé(s), {errors} en erreur ({mode}).")
        if not options["dry_run"]:
            self._report(
                "instance",
                ["template", "copie", "parent (copie)", "ignoré", "statut", "champs non copiés", "avertissements", "erreur"],
                rows,
            )
