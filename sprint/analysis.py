"""
The AI analysis of a management report: the dossier given to the AI, the prompt, and the parsing of
its answer. Providers are pluggable; today the analysis is pasted by hand (Copilot) or skipped.
The dossier holds titles, assignees and figures only: never comments, never the Jira token.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from django.utils import timezone

from .logged_report import sprint_window
from .report_metrics import age_in_status_days, is_closed, is_started_status, went_back

DESCRIPTION_LIMIT = 500
MAX_RISKS = 3
ORIGIN_LABELS = {"planned": "prévu", "added": "ajouté"}


class AnalysisParseError(ValueError):
    """The AI answer does not follow the imposed format; the message is shown to the user (French)."""


@dataclass
class Risk:
    title: str
    text: str


@dataclass
class Analysis:
    situation: list[str] = field(default_factory=list)
    trend: list[str] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)

    def to_session(self) -> dict:
        return {
            "situation": list(self.situation),
            "trend": list(self.trend),
            "risks": [{"title": risk.title, "text": risk.text} for risk in self.risks],
        }

    @classmethod
    def from_session(cls, data: dict) -> "Analysis":
        return cls(
            situation=list(data.get("situation") or []),
            trend=list(data.get("trend") or []),
            risks=[Risk(risk["title"], risk["text"]) for risk in data.get("risks") or []],
        )


def _focus_entry(data: dict) -> dict:
    focus_id = (data.get("focus") or {}).get("id")
    return next((entry for entry in data["sprints"] if entry["sprint"]["id"] == focus_id), data["sprints"][-1])


def _sprint_end(entry: dict):
    return sprint_window(entry["sprint"], timezone.now())[1]


def _went_back(entry: dict, key: str) -> bool:
    history = (entry.get("histories") or {}).get(key)
    return bool(history) and went_back(history, entry["categories"], _sprint_end(entry))


def risky_keys(data: dict, limit: int = 10) -> list[str]:
    """Oldest in progress, then not started, then went back (focus sprint); the only issues described."""
    entry = _focus_entry(data)
    categories = entry["categories"]
    keys = [item["key"] for item in (data.get("snapshot") or {}).get("oldest") or []]
    issues = entry["scope"]["issues"]
    keys += [
        issue["key"] for issue in issues
        if not is_closed(issue, categories) and not is_started_status(issue["status_end"], categories)
    ]
    keys += [issue["key"] for issue in issues if _went_back(entry, issue["key"])]
    return list(dict.fromkeys(keys))[:limit]


def _number(value) -> str:
    if value is None:
        return "-"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _percent(value) -> str:
    return "-" if value is None else f"{value} %"


def _sprint_block(entry: dict) -> list[str]:
    sprint, metrics = entry["sprint"], entry["metrics"]
    end = _sprint_end(entry)
    lines = [
        f"## {sprint.get('name') or 'Sprint ' + str(sprint['id'])}",
        f"Prévus : {metrics['planned']} | Ajoutés : {metrics['added']} | Terminés : {metrics['closed']} | "
        f"Non démarrés : {metrics['not_started']} | En cours à la fin : {metrics['wip_end']}",
        f"Prévus terminés : {_percent(metrics['percent_planned_closed'])} | "
        f"Part d'ajouts : {_percent(metrics['percent_added'])} | "
        f"Cycle médian (jours ouvrés) : {_number(metrics['cycle_median'])} | "
        f"Cycle P85 (jours ouvrés) : {_number(metrics['cycle_p85'])}",
        "clé | type | titre | assigné | statut début → fin | prévu/ajouté | issue | âge dans le statut (j) | "
        "revenu en arrière",
    ]
    for issue in entry["scope"]["issues"]:
        history = (entry.get("histories") or {}).get(issue["key"])
        age = age_in_status_days(history, end) if history else None
        lines.append(
            " | ".join(
                [
                    issue["key"], issue.get("type") or "-", issue["summary"], issue["assignee"] or "non assigné",
                    f"{issue['status_start'] or '?'} → {issue['status_end'] or '?'}",
                    ORIGIN_LABELS.get(issue["origin"], issue["origin"]), issue["outcome"],
                    _number(None if age is None else round(age, 1)),
                    f"revenu en arrière: {'oui' if _went_back(entry, issue['key']) else 'non'}",
                ]
            )
        )
    return lines


def build_dossier(data: dict, descriptions: dict[str, str]) -> str:
    """French plain text for the AI. Descriptions only for the risky issues, cut to 500 characters."""
    lines = [f"Projet : {data.get('project_name') or '-'}", f"Période : {data['title_period']}", ""]
    for entry in data["sprints"]:
        lines += _sprint_block(entry) + [""]
    snapshot = data.get("snapshot")
    if snapshot:
        lines += [
            f"Kanban à la fin du sprint de référence : {snapshot['wip']} en cours, "
            f"{snapshot['aging']} vieillissants ({_percent(snapshot['aging_percent'])})",
            "",
        ]
    described = [(key, (descriptions.get(key) or "").strip()) for key in risky_keys(data)]
    described = [(key, text) for key, text in described if text]
    if described:
        lines.append("## Tickets à risque (descriptions)")
        for key, text in described:
            lines.append(f"{key} : {text[:DESCRIPTION_LIMIT]}")
    return "\n".join(lines).rstrip() + "\n"


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'"))
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip().casefold()


_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_HEADING = re.compile(r"^\s*#+\s*(.*?)\s*#*\s*$")
SECTIONS = (
    ("situation", "ou en est l'equipe", "OÙ EN EST L'ÉQUIPE"),
    ("trend", "tendance", "TENDANCE"),
    ("risks", "risques", "RISQUES"),
)


class AnalysisProvider:
    key = ""
    label = ""
    needs_prompt = True

    def prompt(self, dossier: str) -> str:
        raise NotImplementedError

    def parse(self, text: str) -> Analysis:
        raise NotImplementedError


class CopilotManualProvider(AnalysisProvider):
    """The user pastes the prompt into Copilot and pastes the answer back."""

    key = "copilot"
    label = "Copilot (copier-coller)"

    def prompt(self, dossier: str) -> str:
        return (
            "Tu es un coach agile qui prépare un rapport pour la direction. Voici le dossier d'un ou plusieurs "
            "sprints. Utilise uniquement les chiffres présents dans le dossier, n'en invente aucun. Écris en "
            "français, sobrement, sans jargon.\n\n"
            "Réponds exactement dans ce format, avec ces titres :\n\n"
            "## OÙ EN EST L'ÉQUIPE\n- puce\n## TENDANCE\n- puce\n## RISQUES\n### Titre du risque\nExplication.\n\n"
            "Contraintes : 5 à 7 puces au total pour les deux premières sections ; au plus 3 risques, chacun "
            "avec un titre court et une explication d'une ou deux phrases.\n\n"
            f"DOSSIER\n=======\n{dossier}"
        )

    def parse(self, text: str) -> Analysis:
        found: dict[str, list[str]] = {}
        current, risk_title = None, None
        risks: list[Risk] = []
        risk_lines: list[str] = []

        def close_risk():
            if risk_title is not None:
                risks.append(Risk(risk_title, " ".join(risk_lines).strip()))

        for line in text.splitlines():
            heading = _HEADING.match(line)
            name = _normalize(heading.group(1)) if heading else None
            section = next((key for key, norm, _ in SECTIONS if norm == name), None)
            if section:
                close_risk()
                risk_title, risk_lines, current = None, [], section
                found.setdefault(section, [])
            elif current is None or not line.strip():
                continue
            elif current == "risks":
                if heading:
                    close_risk()
                    risk_title, risk_lines = heading.group(1).strip(), []
                elif risk_title is not None:
                    risk_lines.append(line.strip())
            else:
                bullet = _BULLET.sub("", line).strip()
                if bullet:
                    found[current].append(bullet)
        close_risk()
        missing = [label for key, _, label in SECTIONS if key not in found]
        if missing:
            raise AnalysisParseError(
                "Réponse inutilisable : il manque le titre " + ", ".join(f"« {label} »" for label in missing)
                + ". Demandez à Copilot de respecter exactement le format imposé."
            )
        return Analysis(found["situation"], found["trend"], risks[:MAX_RISKS])


class NoAIProvider(AnalysisProvider):
    key = "none"
    label = "Sans IA"
    needs_prompt = False

    def prompt(self, dossier: str) -> str:
        return ""

    def parse(self, text: str) -> Analysis:
        return Analysis()


PROVIDERS: dict[str, AnalysisProvider] = {p.key: p for p in (CopilotManualProvider(), NoAIProvider())}
