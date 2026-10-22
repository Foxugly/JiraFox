from __future__ import annotations

from dataclasses import dataclass

from django.utils.translation import gettext as _


BULK_LABELS_SESSION_KEY = "bulk_labels_preview"
DEFAULT_BULK_LIMIT = 50
MAX_BULK_LIMIT = 500


def parse_labels(value: str) -> list[str]:
    labels: list[str] = []
    for raw in (value or "").replace("\n", ",").split(","):
        label = raw.strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def clamp_limit(value, default: int = DEFAULT_BULK_LIMIT) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return min(MAX_BULK_LIMIT, max(1, parsed))


def issue_labels(issue: dict) -> list[str]:
    labels = issue.get("labels")
    if isinstance(labels, list):
        return [str(label) for label in labels]
    return []


def build_label_plan(issue: dict, add_labels: list[str], remove_labels: list[str]) -> dict:
    current = issue_labels(issue)
    next_labels = [label for label in current if label not in remove_labels]
    for label in add_labels:
        if label not in next_labels:
            next_labels.append(label)

    return {
        "key": issue.get("key"),
        "summary": issue.get("summary") or "",
        "state": issue.get("state") or "",
        "statusCategory": issue.get("statusCategory") or "",
        "url": issue.get("url") or "",
        "current_labels": current,
        "add_labels": [label for label in add_labels if label not in current],
        "remove_labels": [label for label in remove_labels if label in current],
        "next_labels": next_labels,
        "changed": current != next_labels,
    }


@dataclass
class BulkLabelPreview:
    jql: str
    add_labels: list[str]
    remove_labels: list[str]
    limit: int
    issues: list[dict]
    items: list[dict]

    @property
    def changed_count(self) -> int:
        return sum(1 for item in self.items if item["changed"])

    def as_session_payload(self) -> dict:
        return {
            "jql": self.jql,
            "add_labels": self.add_labels,
            "remove_labels": self.remove_labels,
            "limit": self.limit,
            "items": self.items,
        }


def build_bulk_label_preview(jira, *, jql: str, add_labels_raw: str, remove_labels_raw: str, limit) -> BulkLabelPreview:
    add_labels = parse_labels(add_labels_raw)
    remove_labels = parse_labels(remove_labels_raw)
    limit = clamp_limit(limit)
    issues = jira.run_jql(jql, max_results=limit, full=False)
    items = [build_label_plan(issue, add_labels, remove_labels) for issue in issues]
    return BulkLabelPreview(
        jql=jql,
        add_labels=add_labels,
        remove_labels=remove_labels,
        limit=limit,
        issues=issues,
        items=items,
    )


def apply_bulk_label_preview(jira, payload: dict) -> list[dict]:
    results: list[dict] = []
    for item in payload.get("items", []):
        issue_key = item.get("key")
        if not issue_key:
            continue
        if not item.get("changed"):
            results.append({**item, "ok": True, "skipped": True, "message": _("No change")})
            continue

        try:
            jira.update_issue_labels(
                issue_key,
                add_labels=payload.get("add_labels", []),
                remove_labels=payload.get("remove_labels", []),
                current_labels=item.get("current_labels", []),
            )
            results.append({**item, "ok": True, "skipped": False, "message": _("Updated")})
        except Exception as exc:  # noqa: BLE001
            results.append({**item, "ok": False, "skipped": False, "message": str(exc)})

    return results
