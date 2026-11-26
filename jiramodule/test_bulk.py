"""jira_bulk: walk to a status, instance a template sprint. A fake Jira, never a real one."""

import re
from io import StringIO
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from jiramodule.management.commands.jira_bulk import csv_cell
from jiramodule.services.bulk import (
    InstanceCloner,
    parse_path,
    path_to,
    remaining_steps,
    sprint_ids,
    to_write_value,
    walk_to_status,
)

SPRINT_F = "customfield_10100"
EPIC_F = "customfield_10101"
SP_F = "customfield_10102"
RANK_F = "customfield_10103"

# The workflow of the request: To Do -> Canceled -> Open -> Backlog -> Ready for sprint.
WORKFLOW = {
    "To Do": ["Canceled", "In Progress"],
    "Canceled": ["Open"],
    "Open": ["Backlog"],
    "Backlog": ["Ready for sprint"],
    "Ready for sprint": ["In Progress"],
    "In Progress": ["Done"],
}
VIA = "Canceled>Open>Backlog"


class FakeIssue:
    def __init__(self, key, fields):
        self.key = key
        self.raw = {"key": key, "fields": fields}


class Page(list):
    def __init__(self, items, total):
        super().__init__(items)
        self.total = total


class FakeJira:
    def __init__(self):
        self.issues: dict[str, dict] = {}
        self.links: list[dict] = []
        self.comments_by: dict[str, list] = {}
        self.watchers_by: dict[str, list[str]] = {}
        self.attachments: dict[str, bytes] = {}
        self.added_attachments: list[tuple[str, str, bytes]] = []
        self.transitioned: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.next_id = 100
        self.refuse_on_create: set[str] = set()
        self.cancel_sets_resolution = False
        self.security_on_create_screen = True

    # -- setup -------------------------------------------------------------------------------

    def add(self, key, *, status="To Do", itype="Story", sprint=None, epic=None, parent=None, **extra):
        fields = {
            "project": {"key": "BKL", "id": "1"},
            "issuetype": {"id": "3" if itype == "Sub-task" else ("6" if itype == "Epic" else "1"), "name": itype},
            "status": {"name": status},
            "summary": f"summary {key}",
            "resolution": None,
            "subtasks": [],
            "attachment": [],
            "issuelinks": [],
            SPRINT_F: [f"com.atlassian.greenhopper.service.sprint.Sprint@1a[id={sprint},state=ACTIVE]"] if sprint else None,
            EPIC_F: epic,
            RANK_F: "0|i0001:",
        }
        if parent:
            fields["parent"] = {"key": parent}
            self.issues[parent]["subtasks"].append({"key": key})
        fields.update(extra)
        self.issues[key] = fields
        return key

    # -- Jira API used by bulk.py ------------------------------------------------------------

    def fields(self):
        return [
            {"id": "summary", "schema": {"type": "string", "system": "summary"}},
            {"id": "description", "schema": {"type": "string", "system": "description"}},
            {"id": "assignee", "schema": {"type": "user", "system": "assignee"}},
            {"id": "reporter", "schema": {"type": "user", "system": "reporter"}},
            {"id": "priority", "schema": {"type": "priority", "system": "priority"}},
            {"id": "labels", "schema": {"type": "array", "items": "string", "system": "labels"}},
            {"id": "components", "schema": {"type": "array", "items": "component", "system": "components"}},
            {"id": "fixVersions", "schema": {"type": "array", "items": "version", "system": "fixVersions"}},
            {"id": "timetracking", "schema": {"type": "timetracking", "system": "timetracking"}},
            {"id": SPRINT_F, "schema": {"type": "array", "items": "string", "custom": "com.pyxis.greenhopper.jira:gh-sprint"}},
            {"id": EPIC_F, "schema": {"type": "any", "custom": "com.pyxis.greenhopper.jira:gh-epic-link"}},
            {"id": SP_F, "schema": {"type": "number", "custom": "com.atlassian.jira.plugin.system.customfieldtypes:float"}},
            {"id": RANK_F, "schema": {"type": "any", "custom": "com.pyxis.greenhopper.jira:gh-lexo-rank"}},
            {"id": "security", "schema": {"type": "securitylevel", "system": "security"}},
        ]

    def createmeta_fieldtypes(self, project, issuetype_id, startAt=0, maxResults=50):
        ids = ["summary", "description", "assignee", "reporter", "priority", "labels", "components", "fixVersions", "timetracking", SP_F]
        if self.security_on_create_screen:
            ids.append("security")
        if issuetype_id == "3":
            ids.append("parent")
        else:
            ids += [SPRINT_F, EPIC_F]
        by_id = {f["id"]: f for f in self.fields()}
        values = [{"fieldId": i, "schema": by_id.get(i, {}).get("schema", {})} for i in ids]
        return {"values": values[startAt:startAt + maxResults], "total": len(values), "isLast": startAt + maxResults >= len(values)}

    def issue(self, key, fields=None):
        fields_ = self.issues[key]
        if key not in self.issues:
            raise KeyError(key)
        out = dict(fields_)
        out["issuelinks"] = [self._link_view(link, key) for link in self.links if key in (link["inward"], link["outward"])]
        return FakeIssue(key, out)

    def _link_view(self, link, key):
        # Jira's view: on the source ("inwardIssue" of the POST) the other issue is the outwardIssue.
        if key == link["inward"]:
            return {"id": link["id"], "type": {"name": link["type"]}, "outwardIssue": {"key": link["outward"]}}
        return {"id": link["id"], "type": {"name": link["type"]}, "inwardIssue": {"key": link["inward"]}}

    def search_issues(self, jql, startAt=0, maxResults=50, fields=None, **kwargs):
        keys = self._query(jql)
        page = [self.issue(k) for k in keys[startAt:startAt + maxResults]]
        return Page(page, len(keys))

    def _query(self, jql):
        m = re.search(r'issue in linkedIssues\("([^"]+)", "is cloned by"\) AND (.*)', jql)
        if m:
            original, scope = m.groups()
            clones = [l["inward"] for l in self.links if l["type"] == "Cloners" and l["outward"] == original]
            return [c for c in clones if self._match(c, scope)]
        m = re.match(r"key in \((.*)\)", jql)
        if m:
            return [k.strip().strip('"') for k in m.group(1).split(",")]
        base = jql.split(" ORDER BY")[0]
        return [k for k in self.issues if self._match(k, base)]

    def _match(self, key, clause):
        fields = self.issues[key]
        for part in re.split(r"\s+AND\s+", clause.strip("()")):
            part = part.strip().strip("()")
            if part.startswith("sprint ="):
                if int(part.split("=")[1]) not in sprint_ids(fields.get(SPRINT_F)):
                    return False
            elif part.startswith("parent ="):
                if (fields.get("parent") or {}).get("key") != part.split("=")[1].strip().strip('"'):
                    return False
            elif part.startswith("status ="):
                if fields["status"]["name"] != part.split("=")[1].strip().strip('"'):
                    return False
            elif "standardIssueTypes" in part:
                if fields["issuetype"]["name"] == "Sub-task":
                    return False
        return True

    def transitions(self, key):
        status = self.issues[key]["status"]["name"]
        return [{"id": f"{status}->{to}", "name": f"Go {to}", "to": {"name": to}} for to in WORKFLOW.get(status, [])]

    def transition_issue(self, key, transition_id):
        frm, to = transition_id.split("->")
        assert self.issues[key]["status"]["name"] == frm
        self.issues[key]["status"] = {"name": to}
        if to == "Canceled" and self.cancel_sets_resolution:
            self.issues[key]["resolution"] = {"name": "Won't Do"}
        self.transitioned.append((key, to))

    def create_issue(self, fields):
        refused = {f: "not allowed" for f in self.refuse_on_create if f in fields}
        if refused:
            raise SimpleNamespace_error(refused)
        self.created.append(dict(fields))
        key = f"BKL-{self.next_id}"
        self.next_id += 1
        itype = {"1": "Story", "3": "Sub-task", "6": "Epic"}[fields["issuetype"]["id"]]
        parent = (fields.get("parent") or {}).get("key")
        extra = {k: v for k, v in fields.items() if k not in {"project", "issuetype", "parent", SPRINT_F, EPIC_F}}
        sprint = fields.get(SPRINT_F)
        self.add(key, itype=itype, sprint=sprint, epic=fields.get(EPIC_F), parent=parent, **extra)
        if parent and self.issues[parent].get(SPRINT_F):
            self.issues[key][SPRINT_F] = self.issues[parent][SPRINT_F]
        return SimpleNamespace(key=key)

    def create_issue_link(self, type, inwardIssue, outwardIssue):
        self.next_id += 1
        self.links.append({"id": str(self.next_id), "type": type, "inward": inwardIssue, "outward": outwardIssue})

    def delete_issue_link(self, id):
        self.links = [l for l in self.links if l["id"] != id]

    def comments(self, key):
        return self.comments_by.get(key, [])

    def add_comment(self, key, body, visibility=None):
        self.comments_by.setdefault(key, []).append(
            SimpleNamespace(body=body, author=SimpleNamespace(displayName="Bot"), created="2026-11-26", visibility=None)
        )

    def watchers(self, key):
        return SimpleNamespace(watchers=[SimpleNamespace(name=n) for n in self.watchers_by.get(key, [])])

    def add_watcher(self, key, name):
        self.watchers_by.setdefault(key, []).append(name)

    def attachment(self, id):
        return SimpleNamespace(get=lambda: self.attachments[id])

    def add_attachment(self, key, fileobj, filename=None):
        self.added_attachments.append((key, filename, fileobj.read()))


class SimpleNamespace_error(Exception):  # noqa: N801 - mimics a JIRAError carrying a 400 body
    def __init__(self, errors):
        super().__init__("400")
        self.text = str(errors)
        self.response = SimpleNamespace(json=lambda: {"errors": errors})


# --- paths --------------------------------------------------------------------------------------


def test_parse_path_appends_target_once():
    assert parse_path(VIA, "Ready for sprint") == ["Canceled", "Open", "Backlog", "Ready for sprint"]
    assert parse_path("Canceled>Ready for sprint", "ready for sprint") == ["Canceled", "Ready for sprint"]
    assert parse_path(None, "Done") == ["Done"]


def test_remaining_steps_resumes_after_current_status():
    steps = parse_path(VIA, "Ready for sprint")
    assert remaining_steps("To Do", steps) == steps
    assert remaining_steps("Open", steps) == ["Backlog", "Ready for sprint"]
    assert remaining_steps("Ready for sprint", steps) == []


def test_path_to_stops_at_a_via_step():
    via = ["Canceled", "Open", "Backlog"]
    assert path_to("Open", via) == ["Canceled", "Open"]
    assert path_to("Ready for sprint", via) == [*via, "Ready for sprint"]


# --- walk ---------------------------------------------------------------------------------------


def test_walk_follows_the_whole_path():
    jira = FakeJira()
    jira.add("BKL-1")
    result = walk_to_status(jira, "BKL-1", "To Do", parse_path(VIA, "Ready for sprint"), dry_run=False)
    assert result.ok and result.end == "Ready for sprint"
    assert [to for _, to in jira.transitioned] == ["Canceled", "Open", "Backlog", "Ready for sprint"]


def test_walk_dry_run_changes_nothing():
    jira = FakeJira()
    jira.add("BKL-1")
    result = walk_to_status(jira, "BKL-1", "To Do", parse_path(VIA, "Ready for sprint"), dry_run=True)
    assert result.ok and result.end == "Ready for sprint"
    assert jira.transitioned == []


def test_walk_stops_on_missing_transition():
    jira = FakeJira()
    jira.add("BKL-1")
    result = walk_to_status(jira, "BKL-1", "To Do", parse_path(None, "Ready for sprint"), dry_run=False)
    assert not result.ok and "To Do" in result.error
    assert jira.transitioned == []


def test_walk_reports_a_resolution_left_by_canceled():
    jira = FakeJira()
    jira.cancel_sets_resolution = True
    jira.add("BKL-1")
    result = walk_to_status(jira, "BKL-1", "To Do", parse_path(VIA, "Ready for sprint"), dry_run=False)
    assert result.resolution == "Won't Do"


# --- conversions --------------------------------------------------------------------------------


def test_to_write_value():
    assert to_write_value({"name": "jdoe", "displayName": "J", "key": "jdoe"}, {"type": "user"}) == {"name": "jdoe"}
    assert to_write_value({"id": "2", "name": "High", "self": "x"}, {"type": "priority"}) == {"id": "2"}
    assert to_write_value([{"id": "10", "name": "v1"}], {"type": "array", "items": "version"}) == [{"id": "10"}]
    assert to_write_value(["a", "b"], {"type": "array", "items": "string"}) == ["a", "b"]
    assert to_write_value({"originalEstimate": "2d", "remainingEstimate": "1d"}, {"type": "timetracking"}) == {"originalEstimate": "2d"}
    assert to_write_value({"id": "1", "value": "x", "child": {"id": "5", "value": "y"}}, {"type": "option-with-child"}) == {"id": "1", "child": {"id": "5"}}
    assert to_write_value([{"key": "X"}], {"type": "array", "items": "issuelinks"}) is None
    assert to_write_value(3.0, {"type": "number"}) == 3.0


def test_sprint_ids():
    assert sprint_ids(["com.atlassian.greenhopper.service.sprint.Sprint@1[id=12,rapidViewId=3]"]) == {12}
    assert sprint_ids([{"id": 7}, 8]) == {7, 8}
    assert sprint_ids(None) == set()


# --- instance -----------------------------------------------------------------------------------


def _template():
    jira = FakeJira()
    jira.add("BKL-E", itype="Epic")
    jira.add("BKL-OLD", itype="Epic")
    jira.add(
        "BKL-1",
        status="Ready for sprint",
        sprint=5,
        epic="BKL-OLD",
        assignee={"name": "jdoe", "displayName": "J Doe", "key": "jdoe"},
        reporter={"name": "boss", "displayName": "Boss", "key": "boss"},
        priority={"id": "2", "name": "High"},
        labels=["recurrent"],
        description="desc",
        timetracking={"originalEstimate": "1d", "remainingEstimate": "4h"},
        attachment=[{"id": "a1", "filename": "plan.xlsx"}],
        **{SP_F: 3.0},
    )
    jira.add("BKL-2", status="To Do", sprint=5)
    jira.add("BKL-3", status="Ready for sprint", sprint=99)  # another sprint: not part of the template
    jira.add("BKL-1a", itype="Sub-task", status="Open", parent="BKL-1", sprint=5)
    jira.attachments["a1"] = b"xlsx-bytes"
    jira.comments_by["BKL-1"] = [SimpleNamespace(body="hello", author=SimpleNamespace(displayName="Ann"), created="2026-01-02T10:00", visibility=None)]
    jira.watchers_by["BKL-1"] = ["ann", "bob"]
    return jira


def _run(jira, dry_run=False):
    cloner = InstanceCloner(jira, jql="sprint = 5 ORDER BY Rank ASC", new_sprint_id=6, epic="BKL-E", via=VIA)
    return cloner.run(dry_run=dry_run, on_result=lambda r: None)


def test_instance_clones_into_new_sprint_and_epic_only():
    jira = _template()
    results = _run(jira)
    by_original = {r.original: r for r in results}
    assert set(by_original) == {"BKL-1", "BKL-2", "BKL-1a"}
    assert not any(r.error for r in results)

    clone = jira.issues[by_original["BKL-1"].clone]
    assert sprint_ids(clone[SPRINT_F]) == {6}  # never the template sprint
    assert clone[EPIC_F] == "BKL-E"
    assert clone["summary"] == "summary BKL-1"
    assert clone["assignee"] == {"name": "jdoe"} and clone["reporter"] == {"name": "boss"}
    assert clone["priority"] == {"id": "2"} and clone["labels"] == ["recurrent"] and clone[SP_F] == 3.0
    assert clone["timetracking"] == {"originalEstimate": "1d"}
    assert clone["status"]["name"] == "Ready for sprint"  # same status as its template
    assert jira.issues["BKL-1"][SPRINT_F][0].endswith("[id=5,state=ACTIVE]")  # template untouched
    assert sprint_ids(jira.issues["BKL-1"][SPRINT_F]) == {5}


def test_instance_links_copy_clones_template():
    jira = _template()
    results = _run(jira)
    clone_key = next(r.clone for r in results if r.original == "BKL-1")
    links = jira.issue(clone_key).raw["fields"]["issuelinks"]
    assert {"id": links[0]["id"], "type": {"name": "Cloners"}, "outwardIssue": {"key": "BKL-1"}} in links


def test_instance_keeps_template_status_for_each_issue():
    jira = _template()
    results = {r.original: r for r in _run(jira)}
    assert jira.issues[results["BKL-2"].clone]["status"]["name"] == "To Do"
    assert jira.issues[results["BKL-1a"].clone]["status"]["name"] == "Open"


def test_instance_copies_subtasks_comments_attachments_watchers():
    jira = _template()
    results = {r.original: r for r in _run(jira)}
    parent_clone = results["BKL-1"].clone
    sub_clone = results["BKL-1a"].clone
    assert jira.issues[sub_clone]["parent"] == {"key": parent_clone}
    assert jira.issues[parent_clone]["subtasks"] == [{"key": sub_clone}]
    assert "hello" in jira.comments_by[parent_clone][0].body and "Ann" in jira.comments_by[parent_clone][0].body
    assert (parent_clone, "plan.xlsx", b"xlsx-bytes") in jira.added_attachments
    assert jira.watchers_by[parent_clone] == ["ann", "bob"]


def test_instance_replaces_text_in_summary():
    jira = _template()
    jira.issues["BKL-1"]["summary"] = "[20XX/01] Clôture [20XX/"
    jira.issues["BKL-1a"]["summary"] = "sub [20XX/02]"
    cloner = InstanceCloner(
        jira, jql="sprint = 5", new_sprint_id=6, epic="BKL-E", via=VIA, replacements=[("[20XX/", "[2027/")]
    )
    results = {r.original: r for r in cloner.run(dry_run=False, on_result=lambda r: None)}
    assert jira.issues[results["BKL-1"].clone]["summary"] == "[2027/01] Clôture [2027/"
    assert jira.issues[results["BKL-1a"].clone]["summary"] == "sub [2027/02]"
    assert jira.issues["BKL-1"]["summary"] == "[20XX/01] Clôture [20XX/"  # template untouched
    assert any("summary unchanged" in w for w in results["BKL-2"].warnings)  # no [20XX/ in it
    assert not any("summary unchanged" in w for w in results["BKL-1"].warnings)


def test_instance_rerun_creates_nothing():
    jira = _template()
    _run(jira)
    created = len(jira.created)
    results = _run(jira)
    assert len(jira.created) == created
    assert all(r.skipped == "already cloned" for r in results)


def test_instance_dry_run_writes_nothing():
    jira = _template()
    results = _run(jira, dry_run=True)
    assert jira.created == [] and jira.links == [] and jira.transitioned == []
    assert {r.original for r in results} == {"BKL-1", "BKL-2", "BKL-1a"}
    assert next(r for r in results if r.original == "BKL-1").status.end == "Ready for sprint"


def test_instance_retries_without_a_refused_field():
    jira = _template()
    jira.refuse_on_create = {"reporter"}
    results = {r.original: r for r in _run(jira)}
    assert not results["BKL-1"].error
    assert any(d.startswith("reporter") for d in results["BKL-1"].dropped_fields)


def test_instance_rejects_a_non_epic():
    jira = _template()
    cloner = InstanceCloner(jira, jql="sprint = 5", new_sprint_id=6, epic="BKL-2", via=VIA)
    with pytest.raises(RuntimeError, match="not an epic"):
        cloner.run(dry_run=True, on_result=lambda r: None)


# --- command ------------------------------------------------------------------------------------


def _call(jira, *args):
    out = StringIO()
    with mock.patch("jiramodule.management.commands.jira_bulk.Command._jira", return_value=jira):
        call_command("jira_bulk", *args, stdout=out)
    return out.getvalue()


def test_command_transition_dry_run_filters_from_status():
    jira = _template()
    out = _call(jira, "transition", "sprint = 5", "--from", "To Do", "--to", "Ready for sprint", "--via", VIA, "--dry-run")
    assert "1 ticket(s)" in out and "BKL-2: To Do -> Ready for sprint" in out
    assert jira.transitioned == []


def test_command_transition_needs_oui(monkeypatch):
    jira = _template()
    monkeypatch.setattr("builtins.input", lambda prompt: "non")
    with pytest.raises(CommandError):
        _call(jira, "transition", "sprint = 5", "--from", "To Do", "--to", "Ready for sprint", "--via", VIA)
    assert jira.transitioned == []


def test_command_transition_real_run_writes_report(monkeypatch, tmp_path, settings):
    settings.BASE_DIR = tmp_path
    jira = _template()
    monkeypatch.setattr("builtins.input", lambda prompt: "oui")
    out = _call(jira, "transition", "sprint = 5", "--from", "To Do", "--to", "Ready for sprint", "--via", VIA)
    assert jira.issues["BKL-2"]["status"]["name"] == "Ready for sprint"
    assert "1 OK, 0 en erreur" in out
    assert list((tmp_path / "logs" / "jira_bulk").glob("*-transition.csv"))


def test_instance_skips_issues_already_in_the_target_sprint():
    jira = _template()
    cloner = InstanceCloner(jira, jql="sprint = 5", new_sprint_id=5, epic="BKL-E", via=VIA)
    results = cloner.run(dry_run=False, on_result=lambda r: None)
    assert jira.created == [] and all(r.skipped == "already in the target sprint" for r in results)


def test_originals_jql_keeps_order_and_drops_subtasks():
    jira = FakeJira()
    cloner = InstanceCloner(jira, jql='project = BKL and labels = rec order by created DESC', new_sprint_id=6, epic="E", via=None)
    assert cloner.originals_jql() == (
        "(project = BKL and labels = rec) AND issuetype in standardIssueTypes() ORDER BY created DESC"
    )
    cloner.jql = "sprint = 5"
    assert cloner.originals_jql().endswith("ORDER BY Rank ASC")


def test_command_instance_dry_run():
    jira = _template()
    jira.issues["BKL-2"]["summary"] = "[20XX/03] Paie"
    out = _call(
        jira, "instance", "sprint = 5", "--epic", "BKL-E", "--sprint", "6", "--via", VIA,
        "--replace", "[20XX/", "[2027/", "--dry-run",
    )
    assert "« [2027/03] Paie »" in out
    assert "2 ticket(s) -> sprint 6, epic BKL-E" in out
    assert "simulation" in out and jira.created == []


# --- security -----------------------------------------------------------------------------------


def test_instance_keeps_the_security_level():
    jira = _template()
    jira.issues["BKL-1"]["security"] = {"id": "10000", "name": "Internal", "self": "x"}
    results = {r.original: r for r in _run(jira)}
    assert not results["BKL-1"].error
    assert jira.issues[results["BKL-1"].clone]["security"] == {"id": "10000"}


def test_instance_never_clones_a_restricted_issue_without_its_level():
    jira = _template()
    jira.security_on_create_screen = False
    jira.issues["BKL-1"]["security"] = {"id": "10000", "name": "Internal"}
    results = {r.original: r for r in _run(jira)}
    assert "security level" in results["BKL-1"].error and not results["BKL-1"].clone
    assert all(c["summary"] != "summary BKL-1" for c in jira.created)
    assert "BKL-1a" not in results  # its sub-tasks are not cloned either


def test_instance_security_field_is_not_droppable():
    jira = _template()
    jira.issues["BKL-1"]["security"] = {"id": "10000", "name": "Internal"}
    jira.refuse_on_create = {"security"}
    results = {r.original: r for r in _run(jira)}
    assert results["BKL-1"].error and not results["BKL-1"].clone


def test_csv_cell_neutralises_formulas():
    assert csv_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert csv_cell("@x") == "'@x" and csv_cell("-1") == "'-1"
    assert csv_cell("BKL-1") == "BKL-1" and csv_cell(None) == ""
