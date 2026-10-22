from __future__ import annotations

from dataclasses import dataclass, field


STANDARD_ISSUE_TYPES = "issuetype in standardIssueTypes()"


def quote_value(value: str | int) -> str:
    text = str(value)
    if text.isdigit():
        return text
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"'


@dataclass
class JqlBuilder:
    clauses: list[str] = field(default_factory=list)
    order_clause: str = ""

    @classmethod
    def sprint(cls, sprint_id: int) -> "JqlBuilder":
        return cls().where(f"sprint = {int(sprint_id)}")

    @classmethod
    def project(cls, project_id_or_key: str | int) -> "JqlBuilder":
        return cls().where(f"project = {quote_value(project_id_or_key)}")

    def where(self, clause: str | None) -> "JqlBuilder":
        clause = (clause or "").strip()
        if clause:
            self.clauses.append(clause)
        return self

    def standard_issue_types(self) -> "JqlBuilder":
        return self.where(STANDARD_ISSUE_TYPES)

    def status_category(self, category: str) -> "JqlBuilder":
        return self.where(f"statusCategory = {quote_value(category)}")

    def status_category_not(self, category: str) -> "JqlBuilder":
        return self.where(f"statusCategory != {quote_value(category)}")

    def labels_in(self, labels: list[str]) -> "JqlBuilder":
        values = ", ".join(quote_value(label) for label in labels)
        return self.where(f"labels in ({values})")

    def resolved_since_days(self, days: int) -> "JqlBuilder":
        return self.where(f"resolved >= -{int(days)}d")

    def order_by(self, expression: str) -> "JqlBuilder":
        expression = (expression or "").strip()
        self.order_clause = f"ORDER BY {expression}" if expression else ""
        return self

    def build(self) -> str:
        jql = " AND ".join(f"({clause})" for clause in self.clauses)
        if self.order_clause:
            return f"{jql} {self.order_clause}" if jql else self.order_clause
        return jql
