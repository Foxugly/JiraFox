from datetime import datetime, time, timedelta
from typing import Optional, Tuple, List, Any, Dict

from django.utils import timezone
from django.utils.formats import date_format

from jiramodule.utils.datetime_utils import serialize_dt, _parse_jira_dt, hours_to_business_days


class KanbanMetricsService:
    def __init__(self, tz=None):
        self.tz = tz or timezone.get_default_timezone()

    def business_hours_between(self, start: datetime, end: datetime) -> float:
        """
        Compute business hours between start and end with daily caps:
          - Mon-Thu: 8h/day
          - Fri: 4h/day
          - Sat-Sun: 0h/day

        This is a *daily cap* model (not 9-17 schedule). If any part of a weekday is included,
        we count up to the cap proportionally to the fraction of that day included.
        """
        if start is None or end is None:
            return 0.0

        tz = timezone.get_default_timezone()
        if timezone.is_naive(start):
            start = timezone.make_aware(start, tz)
        else:
            start = start.astimezone(tz)

        if timezone.is_naive(end):
            end = timezone.make_aware(end, tz)
        else:
            end = end.astimezone(tz)

        if end <= start:
            return 0.0

        def daily_cap(dt: datetime) -> float:
            wd = dt.weekday()  # 0=Mon ... 4=Fri ... 6=Sun
            if wd <= 3:
                return 8.0
            if wd == 4:
                return 4.0
            return 0.0

        total = 0.0
        cur = start

        while cur.date() <= end.date():
            day_start = timezone.make_aware(datetime.combine(cur.date(), time.min), tz)
            day_end = timezone.make_aware(datetime.combine(cur.date(), time.max), tz)

            seg_start = max(cur, day_start)
            seg_end = min(end, day_end)

            if seg_end > seg_start:
                cap = daily_cap(seg_start)
                if cap > 0:
                    # fraction of the day covered by [seg_start, seg_end]
                    day_seconds = 24 * 3600
                    seg_seconds = (seg_end - seg_start).total_seconds()
                    frac = max(0.0, min(1.0, seg_seconds / day_seconds))
                    total += cap * frac

            # move to next day
            cur = datetime.combine(cur.date() + timedelta(days=1), time.min, tzinfo=tz)

        return round(total, 2)

    def compute(self, issue: Any) -> Dict[str, Any]:
        """
        "ses metriques kanban" — Jira doesn't expose cycle/lead time directly via python-jira.
        We compute pragmatic metrics from changelog:
        - lead_time_hours: created -> resolution (or now)
        - cycle_time_hours: first time moved into an 'In Progress' category -> resolution (or now)
        - time_in_status_hours: dict status -> hours
        - current_wip_age_hours: last status change -> now
        """
        created = _parse_jira_dt(getattr(issue.fields, "created", None))
        resolved = _parse_jira_dt(getattr(issue.fields, "resolutiondate", None))
        tz = timezone.get_default_timezone()
        now = datetime.now(tz)

        end = resolved or now

        lead_time_hours = None
        if created:
            lead_time_hours = self.business_hours_between(created.astimezone(tz), end.astimezone(tz))

        # Build status change timeline from changelog
        histories = getattr(getattr(issue, "changelog", None), "histories", None) or []
        changes: List[Tuple[datetime, str, str]] = []  # (when, from, to)

        for h in histories:
            when = _parse_jira_dt(getattr(h, "created", None))
            if not when:
                continue
            for it in getattr(h, "items", []) or []:
                if getattr(it, "field", "") == "status":
                    changes.append((when, getattr(it, "fromString", None), getattr(it, "toString", None)))

        changes.sort(key=lambda x: x[0])

        # Compute time in status
        time_in_status_sec: Dict[str, float] = {}
        last_status = getattr(getattr(issue.fields, "status", None), "name", None)
        # We need the initial status: use first transition's fromString if available
        if changes:
            initial_status = changes[0][1] or last_status
        else:
            initial_status = last_status

        # timeline points: (t, status)
        points: List[Tuple[datetime, str]] = []
        if created and initial_status:
            points.append((created.astimezone(tz), initial_status))

        for when, _from, to in changes:
            if to:
                points.append((when.astimezone(tz), to))

        if not points:
            # no created or no status info
            return {
                "created" : created,
                "lead_time_hours": lead_time_hours,
                "cycle_time_hours": None,
                "first_in_progress": None,
                "first_in_progress_display": None,
                "current_wip_age_hours": None,
                "time_in_status_hours": {},
                "notes": "Pas assez d'infos pour calculer (created/status/changelog manquant).",
            }

        # accumulate durations
        for idx in range(len(points)):
            t0, st0 = points[idx]
            t1 = (points[idx + 1][0] if idx + 1 < len(points) else end.astimezone(tz))
            dur = max(0.0, (t1 - t0).total_seconds())
            if st0:
                time_in_status_sec[st0] = time_in_status_sec.get(st0, 0.0) + dur

        time_in_status_hours = {k: round(v / 3600.0, 2) for k, v in time_in_status_sec.items()}
        max_hours = max(time_in_status_hours.values(), default=0.0)

        time_in_status = []
        for st, hrs in sorted(time_in_status_hours.items(), key=lambda kv: kv[1], reverse=True):
            pct = float(round((hrs / max_hours) * 100, 1) if max_hours else 0.0)
            time_in_status.append({
                "status": st,
                "hours": hrs,
                "days": hours_to_business_days(hrs),
                "pct": pct,
            })
        # Cycle time heuristic: first time status becomes something containing "In Progress"
        # (Your workflow may differ — adjust if needed.)
        first_in_progress: Optional[datetime] = None
        for t, st in points:
            if st and "progress" in st.lower():
                first_in_progress = t
                break

        cycle_time_hours = None
        if first_in_progress:
            cycle_time_hours = self.business_hours_between(first_in_progress, end.astimezone(tz))

        # current WIP age: time since last status point
        current_wip_age_hours = self.business_hours_between(points[-1][0], now)
        lead_time_days = hours_to_business_days(lead_time_hours)
        cycle_time_days = hours_to_business_days(cycle_time_hours)
        current_wip_age_days = hours_to_business_days(current_wip_age_hours)
        d = serialize_dt(first_in_progress)
        created_dt = serialize_dt(created)
        return {
            "created": created_dt["iso"],
            "created_display": created_dt["display"],
            "first_in_progress": d["iso"],
            "first_in_progress_display": d["display"],

            "lead_time_hours": lead_time_hours,
            "lead_time_days": lead_time_days,

            "cycle_time_hours": cycle_time_hours,
            "cycle_time_days": cycle_time_days,

            "current_wip_age_hours": current_wip_age_hours,
            "current_wip_age_days": current_wip_age_days,

            # nouveau : liste enrichie
            "time_in_status": time_in_status,

            # tu peux garder l'ancien dict si tu veux compat
            "time_in_status_hours": time_in_status_hours,

            "heuristics": {
                "cycle_time_start_rule": "first status containing 'In Progress'",
            },
        }

    def history(self, issue: Any) -> List[Dict[str, Any]]:
        """
        Retourne l'historique complet des changements de statut (ordre chronologique).
        Chaque entrée = {at, from, to}
        """
        tz = timezone.get_default_timezone()

        histories = getattr(getattr(issue, "changelog", None), "histories", None) or []
        changes: List[Tuple[datetime, Optional[str], Optional[str]]] = []

        for h in histories:
            when = _parse_jira_dt(getattr(h, "created", None))
            if not when:
                continue
            for it in getattr(h, "items", []) or []:
                if getattr(it, "field", "") == "status":
                    changes.append(
                        (when.astimezone(tz), getattr(it, "fromString", None), getattr(it, "toString", None)))

        changes.sort(key=lambda x: x[0])

        out = []
        for when, from_st, to_st in changes:
            out.append({
                "at": when.isoformat(),
                "at_display": date_format(when, "DATETIME_FORMAT", use_l10n=True),
                "from": from_st,
                "to": to_st,
            })
        return out
