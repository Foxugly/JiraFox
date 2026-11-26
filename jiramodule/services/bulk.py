"""
Bulk operations on Jira issues, driven by the `jira_bulk` management command.

- `walk_to_status`: moves an issue through a list of statuses, one transition at a time
  (Jira only exposes the transitions available from the current status, never the whole
  workflow, so the path is given by the caller).
- `InstanceCloner`: copies every issue of a JQL (fields, sub-tasks, comments,
  attachments, watchers) into another sprint and epic, links each copy to its template
  ("clones"), and brings the copy to the template's status.

Everything takes the `jira.JIRA` client, so the tests drive it with a fake one.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .jql import STANDARD_ISSUE_TYPES, quote_value

CLONERS = "Cloners"
SEARCH_PAGE_SIZE = 100

# Never copied as such: set by Jira, by the workflow, or by the cloner itself.
NEVER_COPIED = {
    "project",
    "issuetype",
    "parent",
    "status",
    "resolution",
    "resolutiondate",
    "created",
    "updated",
    "creator",
    "votes",
    "watches",
    "worklog",
    "issuelinks",
    "attachment",
    "comment",
    "subtasks",
    "aggregatetimespent",
    "aggregatetimeestimate",
    "aggregatetimeoriginalestimate",
    "aggregateprogress",
    "progress",
    "timespent",
    "timeestimate",
    "timeoriginalestimate",
    "lastViewed",
    "statuscategorychangedate",
}
SPRINT_SUFFIX = ":gh-sprint"
EPIC_LINK_SUFFIX = ":gh-epic-link"
# Jira Software fields that only make sense on an epic or are computed.
SKIPPED_CUSTOM_SUFFIXES = (SPRINT_SUFFIX, EPIC_LINK_SUFFIX, ":gh-epic-status", ":gh-epic-label", ":gh-epic-color", ":gh-lexo-rank")


def _same(left: Optional[str], right: Optional[str]) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def _status_name(raw_fields: dict) -> str:
    return ((raw_fields.get("status") or {}).get("name")) or ""


def parse_path(via: Optional[str], target: str) -> list[str]:
    """`"A>B>C"` + target -> ["A", "B", "C", target] (the target is not repeated)."""
    steps = [step.strip() for step in (via or "").split(">") if step.strip()]
    if not steps or not _same(steps[-1], target):
        steps.append(target)
    return steps


def remaining_steps(current_status: str, steps: list[str]) -> list[str]:
    """
    What is left of the path from the current status: an issue already part-way (a previous
    run stopped on it) resumes after the step it is on, an issue already at the target has
    nothing left to do.
    """
    for index in range(len(steps) - 1, -1, -1):
        if _same(steps[index], current_status):
            return steps[index + 1:]
    return list(steps)


def path_to(target: str, via_steps: list[str]) -> list[str]:
    """The path to a given status along the `--via` steps: stop at it when it is one of them."""
    for index, step in enumerate(via_steps):
        if _same(step, target):
            return via_steps[: index + 1]
    return [*via_steps, target]


@dataclass
class WalkResult:
    key: str
    start: str
    end: str
    done: list[str] = field(default_factory=list)
    error: str = ""
    resolution: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _find_transition(transitions: Iterable[dict], step: str) -> Optional[dict]:
    transitions = list(transitions)
    by_target = [t for t in transitions if _same((t.get("to") or {}).get("name"), step)]
    if by_target:
        return by_target[0]
    by_name = [t for t in transitions if _same(t.get("name"), step)]
    return by_name[0] if by_name else None


def walk_to_status(jira: Any, key: str, current_status: str, steps: list[str], *, dry_run: bool) -> WalkResult:
    """
    Follow `steps` from `current_status`. A dry run only checks that the first transition is
    available (the next ones depend on it being done). Stops at the first step that has no
    matching transition or that Jira refuses.
    """
    todo = remaining_steps(current_status, steps)
    result = WalkResult(key=key, start=current_status, end=current_status)
    for step in todo:
        transition = _find_transition(jira.transitions(key), step)
        if transition is None:
            result.error = f'no transition from "{result.end}" to "{step}"'
            return result
        if dry_run:
            result.done = list(todo)
            result.end = todo[-1] if todo else current_status
            return result
        try:
            jira.transition_issue(key, transition["id"])
        except Exception as exc:  # noqa: BLE001 - reported per issue, the run goes on
            result.error = f'"{result.end}" -> "{step}" refused: {_jira_error_text(exc)}'
            return result
        result.done.append(step)
        result.end = step
    if not dry_run and result.done:
        issue = jira.issue(key, fields="status,resolution")
        raw = issue.raw.get("fields") or {}
        result.end = _status_name(raw) or result.end
        result.resolution = ((raw.get("resolution") or {}).get("name")) or ""
    return result


def _jira_error_text(exc: Exception) -> str:
    text = getattr(exc, "text", None) or str(exc)
    return re.sub(r"\s+", " ", str(text)).strip()[:300]


def search_all(jira: Any, jql: str, *, fields: str = "*all") -> list:
    """Every issue of a JQL (paged), in the JQL's order."""
    found: list = []
    start = 0
    while True:
        page = jira.search_issues(jql, startAt=start, maxResults=SEARCH_PAGE_SIZE, fields=fields)
        found.extend(page)
        total = getattr(page, "total", None)
        start += len(page)
        if not page or (total is not None and start >= total) or len(page) < SEARCH_PAGE_SIZE:
            return found


# --- field copy -----------------------------------------------------------------------------


def _ref(value: Any) -> Any:
    """An object field as Jira reads it -> as Jira accepts it on create."""
    if not isinstance(value, dict):
        return value
    if "child" in value and isinstance(value.get("child"), dict):  # cascading select
        return {"id": value.get("id"), "child": {"id": value["child"].get("id")}}
    if "name" in value and ("key" in value or "emailAddress" in value or "displayName" in value) and "id" not in value:
        return {"name": value["name"]}  # user (Server: by name)
    if "id" in value:
        return {"id": value["id"]}
    if "name" in value:
        return {"name": value["name"]}
    if "value" in value:
        return {"value": value["value"]}
    return value


def to_write_value(value: Any, schema: dict) -> Any:
    """Convert a read value to its create form, or None when it cannot be copied safely."""
    if value is None:
        return None
    kind = (schema or {}).get("type")
    items = (schema or {}).get("items")
    system = (schema or {}).get("system")
    if system == "timetracking" or kind == "timetracking":
        original = (value or {}).get("originalEstimate") if isinstance(value, dict) else None
        return {"originalEstimate": original} if original else None
    if kind == "user":
        name = value.get("name") if isinstance(value, dict) else None
        return {"name": name} if name else None
    if kind == "array":
        if not isinstance(value, list):
            return None
        if items in {"string", "number", "date", "datetime"}:
            return list(value)
        if items == "user":
            return [{"name": v.get("name")} for v in value if isinstance(v, dict) and v.get("name")]
        if items in {"option", "version", "component", "group"}:
            return [_ref(v) for v in value]
        return None  # links, attachments, sprints...: never copied generically
    if kind in {"string", "number", "date", "datetime", "any"}:
        return None if isinstance(value, (dict, list)) else value
    if kind in {"option", "priority", "version", "option-with-child", "securitylevel", "group", "project"}:
        return _ref(value)
    return None


def sprint_ids(value: Any) -> set[int]:
    """Sprint ids of a sprint field: Server gives "…Sprint@x[id=12,…]" strings or objects."""
    ids: set[int] = set()
    for item in value or []:
        if isinstance(item, dict) and item.get("id") is not None:
            ids.add(int(item["id"]))
        elif isinstance(item, (int, float)):
            ids.add(int(item))
        else:
            match = re.search(r"\bid=(\d+)", str(item))
            if match:
                ids.add(int(match.group(1)))
    return ids


@dataclass
class CloneResult:
    original: str
    clone: str = ""
    summary: str = ""
    parent_clone: str = ""
    skipped: str = ""
    dropped_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: Optional[WalkResult] = None
    error: str = ""


class InstanceCloner:
    def __init__(
        self,
        jira: Any,
        *,
        jql: str,
        new_sprint_id: int,
        epic: str,
        via: Optional[str],
        replacements: Optional[list[tuple[str, str]]] = None,
    ) -> None:
        self.jira = jira
        # Literal text replacements in the summary of every copy, in order (e.g. "[20XX/" -> "[2027/").
        self.replacements = [(old, new) for old, new in (replacements or []) if old]
        self.jql = jql
        self.new_sprint_id = int(new_sprint_id)
        self.epic_ref = str(epic)
        self.via_steps = [s.strip() for s in (via or "").split(">") if s.strip()]
        self._fields_by_id: Optional[dict[str, dict]] = None
        self._create_fields: dict[tuple[str, str], dict[str, dict]] = {}
        self.epic_key = ""

    # -- metadata ------------------------------------------------------------------------

    @property
    def fields_by_id(self) -> dict[str, dict]:
        if self._fields_by_id is None:
            self._fields_by_id = {f["id"]: f for f in self.jira.fields() if f.get("id")}
        return self._fields_by_id

    def _field_with_suffix(self, suffix: str) -> Optional[str]:
        for field_id, meta in self.fields_by_id.items():
            if ((meta.get("schema") or {}).get("custom") or "").endswith(suffix):
                return field_id
        return None

    @property
    def sprint_field(self) -> str:
        field_id = self._field_with_suffix(SPRINT_SUFFIX)
        if not field_id:
            raise RuntimeError("No Sprint field on this Jira.")
        return field_id

    @property
    def epic_link_field(self) -> str:
        field_id = self._field_with_suffix(EPIC_LINK_SUFFIX)
        if not field_id:
            raise RuntimeError("No Epic Link field on this Jira (Jira Server / Data Center only).")
        return field_id

    def create_fields(self, project: str, issuetype_id: str) -> dict[str, dict]:
        """{field id: meta} of the create screen of a project + issue type."""
        cache_key = (project, issuetype_id)
        if cache_key not in self._create_fields:
            fields: dict[str, dict] = {}
            try:
                start = 0
                while True:
                    page = self.jira.createmeta_fieldtypes(project, issuetype_id, startAt=start, maxResults=50)
                    values = page.get("values") or []
                    for meta in values:
                        fields[meta.get("fieldId") or meta.get("key")] = meta
                    start += len(values)
                    if not values or page.get("isLast", True) or start >= (page.get("total") or 0):
                        break
            except Exception:  # noqa: BLE001 - Jira < 8.4: the old createmeta
                meta = self.jira.createmeta(
                    projectKeys=project, issuetypeIds=[issuetype_id], expand="projects.issuetypes.fields"
                )
                for proj in meta.get("projects") or []:
                    for itype in proj.get("issuetypes") or []:
                        fields.update(itype.get("fields") or {})
            self._create_fields[cache_key] = fields
        return self._create_fields[cache_key]

    def resolve_epic(self) -> str:
        epic = self.jira.issue(self.epic_ref, fields="issuetype,summary")
        if not _same(((epic.raw.get("fields") or {}).get("issuetype") or {}).get("name"), "Epic"):
            raise RuntimeError(f"{epic.key} is not an epic.")
        self.epic_key = epic.key
        return epic.key

    # -- reading -------------------------------------------------------------------------

    def originals_jql(self) -> str:
        """The templates: the JQL's standard issues (sub-tasks come with their parent), in its order or the backlog's."""
        where, order = split_order_by(self.jql)
        where = f"({where}) AND {STANDARD_ISSUE_TYPES}" if where else STANDARD_ISSUE_TYPES
        return f"{where} ORDER BY {order or 'Rank ASC'}"

    def originals(self) -> list:
        return search_all(self.jira, self.originals_jql())

    def subtasks_of(self, issue: Any) -> list:
        keys = [s.get("key") for s in (issue.raw.get("fields") or {}).get("subtasks") or [] if s.get("key")]
        if not keys:
            return []
        jql = f"key in ({', '.join(quote_value(k) for k in keys)}) ORDER BY key ASC"
        return search_all(self.jira, jql)

    def existing_clone(self, original_key: str, extra: str) -> Optional[str]:
        jql = f'issue in linkedIssues({quote_value(original_key)}, "is cloned by") AND {extra}'
        found = self.jira.search_issues(jql, maxResults=1, fields="summary")
        return found[0].key if found else None

    # -- building ------------------------------------------------------------------------

    def build_fields(self, issue: Any, *, parent_key: Optional[str]) -> tuple[dict, list[str]]:
        """The create payload of a copy, and the fields of the template that cannot be copied."""
        raw = issue.raw.get("fields") or {}
        project_key = (raw.get("project") or {}).get("key")
        issuetype = raw.get("issuetype") or {}
        allowed = self.create_fields(project_key, issuetype.get("id"))
        payload: dict[str, Any] = {"project": {"key": project_key}, "issuetype": {"id": issuetype.get("id")}}
        skipped: list[str] = []
        # A restricted template must never give an unrestricted copy: no security level, no clone.
        security = raw.get("security")
        if security:
            if "security" not in allowed or not (security or {}).get("id"):
                raise RuntimeError(
                    f'{issue.key} has the security level "{security.get("name")}", which cannot be set on its copy: not cloned.'
                )
            payload["security"] = {"id": security["id"]}
        for field_id, value in raw.items():
            if field_id in NEVER_COPIED or field_id == "security" or value in (None, [], ""):
                continue
            meta = self.fields_by_id.get(field_id) or {}
            custom = (meta.get("schema") or {}).get("custom") or ""
            if custom.endswith(SKIPPED_CUSTOM_SUFFIXES):
                continue
            if field_id not in allowed:
                skipped.append(field_id)
                continue
            schema = (allowed[field_id].get("schema") or meta.get("schema") or {})
            converted = to_write_value(value, schema)
            if converted in (None, [], {}):
                skipped.append(field_id)
                continue
            payload[field_id] = converted
        summary = payload.get("summary") or raw.get("summary") or ""
        for old, new in self.replacements:
            summary = summary.replace(old, new)
        payload["summary"] = summary
        if parent_key:
            payload["parent"] = {"key": parent_key}
        else:
            if self.sprint_field in allowed:
                payload[self.sprint_field] = self.new_sprint_id
            if self.epic_link_field in allowed:
                payload[self.epic_link_field] = self.epic_key
        return payload, skipped

    # -- writing -------------------------------------------------------------------------

    def _create(self, payload: dict, result: CloneResult) -> Any:
        """Create the issue; on a per-field refusal (e.g. reporter without the permission), retry without those fields."""
        essential = {"project", "issuetype", "summary", "parent", "security", self.sprint_field, self.epic_link_field}
        try:
            return self.jira.create_issue(fields=payload)
        except Exception as exc:  # noqa: BLE001
            errors = _field_errors(exc)
            droppable = [f for f in errors if f in payload and f not in essential]
            if not droppable or len(droppable) < len(errors):
                raise
            for field_id in droppable:
                payload.pop(field_id, None)
                result.dropped_fields.append(f"{field_id} ({errors[field_id]})")
            return self.jira.create_issue(fields=payload)

    def _link(self, clone_key: str, original_key: str) -> None:
        """`clone` clones `original`; checked on the clone because the REST direction is easy to get backwards."""
        self.jira.create_issue_link(CLONERS, clone_key, original_key)
        links = (self.jira.issue(clone_key, fields="issuelinks").raw.get("fields") or {}).get("issuelinks") or []
        for link in links:
            if not _same((link.get("type") or {}).get("name"), CLONERS):
                continue
            if (link.get("outwardIssue") or {}).get("key") == original_key:
                return
            if (link.get("inwardIssue") or {}).get("key") == original_key:
                self.jira.delete_issue_link(link["id"])
                self.jira.create_issue_link(CLONERS, original_key, clone_key)
                return

    def _copy_extras(self, original: Any, clone_key: str, result: CloneResult, *, with_attachments: bool = True) -> None:
        raw = original.raw.get("fields") or {}
        for comment in self.jira.comments(original.key):
            author = getattr(getattr(comment, "author", None), "displayName", "") or ""
            created = (getattr(comment, "created", "") or "")[:10]
            body = f"_{author}, {created}_\n\n{comment.body}" if author else comment.body
            visibility = getattr(comment, "visibility", None)
            visibility = {"type": visibility.type, "value": visibility.value} if visibility else None
            try:
                self.jira.add_comment(clone_key, body, visibility=visibility)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"comment not copied: {_jira_error_text(exc)}")
        if with_attachments:
            for attachment in raw.get("attachment") or []:
                try:
                    content = self.jira.attachment(attachment["id"]).get()
                    self.jira.add_attachment(clone_key, io.BytesIO(content), filename=attachment.get("filename"))
                except Exception as exc:  # noqa: BLE001
                    result.warnings.append(f'attachment "{attachment.get("filename")}" not copied: {_jira_error_text(exc)}')
        try:
            watchers = getattr(self.jira.watchers(original.key), "watchers", []) or []
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"watchers not read: {_jira_error_text(exc)}")
            watchers = []
        for watcher in watchers:
            name = getattr(watcher, "name", None)
            if not name:
                continue
            try:
                self.jira.add_watcher(clone_key, name)
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(f"watcher {name} not added: {_jira_error_text(exc)}")

    def _check_sprint(self, clone_key: str) -> None:
        value = (self.jira.issue(clone_key, fields=self.sprint_field).raw.get("fields") or {}).get(self.sprint_field)
        ids = sprint_ids(value)
        if ids != {self.new_sprint_id}:
            raise RuntimeError(f"{clone_key}: sprint field is {sorted(ids)}, expected only [{self.new_sprint_id}]")

    def _warn_if_unreplaced(self, original: Any, result: "CloneResult") -> None:
        """A --replace that changed nothing usually means a template written differently: say it."""
        if self.replacements and result.summary == ((original.raw.get("fields") or {}).get("summary") or ""):
            olds = ", ".join(f'"{old}"' for old, _ in self.replacements)
            result.warnings.append(f"summary unchanged: {olds} not found")

    def _check_security(self, original: Any, clone_key: str) -> None:
        """Comments and attachments are copied only once the copy is as restricted as its template."""
        expected = ((original.raw.get("fields") or {}).get("security") or {}).get("id")
        actual = ((self.jira.issue(clone_key, fields="security").raw.get("fields") or {}).get("security") or {}).get("id")
        if expected != actual:
            raise RuntimeError(
                f"{clone_key}: security level {actual!r}, expected {expected!r}; comments and attachments not copied"
            )

    def clone_one(self, original: Any, *, parent_key: Optional[str], dry_run: bool) -> CloneResult:
        """Clone a template issue (parent_key=None) or a sub-task under its parent's copy (real runs only)."""
        result = CloneResult(original=original.key, parent_clone=parent_key or "")
        if not parent_key and self.new_sprint_id in sprint_ids((original.raw.get("fields") or {}).get(self.sprint_field)):
            result.skipped = "already in the target sprint"
            return result
        scope = f"parent = {quote_value(parent_key)}" if parent_key else f"sprint = {self.new_sprint_id}"
        existing = self.existing_clone(original.key, scope)
        if existing:
            result.clone = existing
            result.skipped = "already cloned"
            return result
        try:
            payload, skipped = self.build_fields(original, parent_key=parent_key)
        except RuntimeError as exc:
            result.error = str(exc)
            return result
        result.dropped_fields.extend(skipped)
        result.summary = payload["summary"]
        self._warn_if_unreplaced(original, result)
        target_status = _status_name(original.raw.get("fields") or {})
        if dry_run:
            result.status = WalkResult(key="(new)", start="", end=target_status, done=path_to(target_status, self.via_steps))
            return result
        try:
            created = self._create(payload, result)
            result.clone = created.key
            self._link(created.key, original.key)
            if not parent_key:
                self._check_sprint(created.key)
            self._check_security(original, created.key)
            self._copy_extras(original, created.key, result)
            current = _status_name(self.jira.issue(created.key, fields="status").raw.get("fields") or {})
            if not _same(current, target_status):
                result.status = walk_to_status(
                    self.jira, created.key, current, path_to(target_status, self.via_steps), dry_run=False
                )
                if result.status.error:
                    result.warnings.append(f"status: {result.status.error}")
        except Exception as exc:  # noqa: BLE001
            result.error = _jira_error_text(exc)
        return result

    def run(self, *, dry_run: bool, on_result: Callable[[CloneResult], None]) -> list[CloneResult]:
        self.resolve_epic()
        results: list[CloneResult] = []
        for original in self.originals():
            parent = self.clone_one(original, parent_key=None, dry_run=dry_run)
            on_result(parent)
            results.append(parent)
            if parent.error or (not dry_run and not parent.clone):
                continue
            for subtask in self.subtasks_of(original):
                if dry_run:
                    child = CloneResult(original=subtask.key, parent_clone=parent.clone or "(new)")
                    try:
                        child.summary = self.build_fields(subtask, parent_key="(new)")[0]["summary"]
                        self._warn_if_unreplaced(subtask, child)
                    except RuntimeError as exc:  # e.g. a security level the copy could not get
                        child.error = str(exc)
                    status = _status_name(subtask.raw.get("fields") or {})
                    child.status = WalkResult(key="(new)", start="", end=status, done=path_to(status, self.via_steps))
                    if parent.clone:
                        existing = self.existing_clone(subtask.key, f"parent = {quote_value(parent.clone)}")
                        if existing:
                            child.clone, child.skipped = existing, "already cloned"
                else:
                    child = self.clone_one(subtask, parent_key=parent.clone, dry_run=False)
                on_result(child)
                results.append(child)
        return results


def split_order_by(jql: str) -> tuple[str, str]:
    """("where part", "order part") of a JQL; the ORDER BY keyword is case-insensitive."""
    match = re.search(r"\border\s+by\b", jql or "", flags=re.IGNORECASE)
    if not match:
        return (jql or "").strip(), ""
    return jql[: match.start()].strip(), jql[match.end():].strip()


def _field_errors(exc: Exception) -> dict[str, str]:
    """{field: message} of a Jira 400 on create."""
    response = getattr(exc, "response", None)
    try:
        data = response.json() if response is not None else {}
    except Exception:  # noqa: BLE001
        data = {}
    return {k: str(v) for k, v in ((data or {}).get("errors") or {}).items()}
