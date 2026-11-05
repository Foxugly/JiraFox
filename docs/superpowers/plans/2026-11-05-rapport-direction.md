# Rapport direction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JiraFox produces a management PowerPoint (`.pptx`) for one sprint or a set of sprints, with
figures and native charts computed by JiraFox and analysis text written by an AI (manual Copilot) or
left out (no AI).

**Architecture:** Pure metric functions on top of `build_scope_flow` (`sprint/report_metrics.py`),
a Jira-gathering layer that loads and caches one sprint's scope (`sprint/report_data.py`), a pluggable
analysis layer (`sprint/analysis.py`), a deck builder on `python-pptx` (`sprint/pptx_report.py`),
then two pages: the multi-sprint view and the report page (analysis + download).

**Tech Stack:** Django 6.1, Python 3.14, `jira` 3.10, `python-pptx` 1.0.2, Chart.js (already used).

**Spec:** `docs/superpowers/specs/2026-11-05-rapport-direction-design.md`

## Global Constraints

- Local tool: commit and push directly to `main`, no PR, no CI.
- UI strings through `{% trans %}` / `gettext`, msgids in English, translated in `locale/fr` and
  `locale/nl` (`.po` + compiled `.mo`). A `{% trans '…' %}` msgid must not contain an apostrophe.
- PowerPoint content is in French, hard-coded (not translated).
- No velocity anywhere, in tickets or story points.
- Figures on slides always come from JiraFox, never from the AI text.
- The Solidaris template is never committed: uploads go under `MEDIA_ROOT` (`media/`), git-ignored.
- AI disclaimer, verbatim: « Texte généré par IA à partir des métriques JiraFox — à relire avant diffusion »
- Never put the Jira token in an error message, a log or the dossier.
- "Started" / "closed" use the rules of `sprint/scope_flow.completion_rates` (categories + the
  project's `started_statuses`), everywhere, including cycle time.
- Zero denominator → `None` → a gap in charts, "-" in tables.
- Tests: `python manage.py test` must stay green (114 tests today).

## Review Focus

1. **Sprint without start date / without issues / without history** — the report still builds, with
   "-" and gaps, and the speaker notes say why. Test in Task 2 and Task 5.
2. **Copilot answer missing a section or in free text** — a clear error, fields left editable, and
   "generate without AI" still possible. Test in Task 4.
3. **Template uploaded that is not the Solidaris one** (fewer than 6 slides) — refused at upload with
   a message; generation falls back to the neutral layout. Test in Task 1 and Task 5.
4. **Date range with no closed sprint** — the multi-sprint page says so, no report button, no crash.
   Test in Task 6.
5. **Active sprint report** — title marked « (en cours) », trend built from closed sprints only.
   Test in Task 3 and Task 5.

## Rulings (calls not written in the spec)

- **Slide 3 on a multi-sprint report** shows the sums over the period's sprints (planned, added,
  closed, not started), and the key figure is the summed planned closed / summed planned.
- **"Previous 5" for a single-sprint report** = the 5 closed sprints of the board whose end date is
  before the chosen sprint's start, latest first, then shown oldest → newest.
- **Done moment of a closed issue** = the last transition into a done-category status at or before
  the sprint end; issues closed per Jira's bucket without such a transition get no cycle time.
- **Cache:** the per-sprint report data of a **closed** sprint is cached 24 h in Django's cache
  (key with the project pk and sprint id); active sprints are never cached.

---

### Task 1: Dependency, template upload on the project

**Files:**
- Modify: `requirements.txt`, `JiraFox/settings.py`, `JiraFox/urls.py` (nothing to serve: media stays private), `.gitignore`
- Modify: `team/models.py`, `team/forms.py`, `templates/team/project_form.html`
- Create: `team/migrations/0004_report_template.py`, `team/report_template.py`
- Test: `team/tests.py`

**Interfaces:**
- Produces: `Team.report_template` (`FileField(upload_to="report_templates/", blank=True)`),
  `team.report_template.validate_report_template(file) -> None` (raises `ValidationError`),
  `team.report_template.TEMPLATE_SLIDE_COUNT = 6`.

- [ ] **Step 1:** Add `python-pptx==1.0.2` to `requirements.txt`; `pip install -r requirements.txt`.
- [ ] **Step 2:** Settings: `MEDIA_ROOT = BASE_DIR / "media"`, `MEDIA_URL = "/media/"`. `.gitignore`:
  add `media/` under a comment « Uploaded files (PowerPoint templates): never committed. ». No URL
  serves media: the file is only read server-side.
- [ ] **Step 3: Write the failing tests** in `team/tests.py`:
  - `test_report_template_accepts_a_six_slide_deck`: a deck built in-test with `python-pptx`
    (6 blank slides) saved via the project form → `project.report_template.name` starts with
    `report_templates/`.
  - `test_report_template_refuses_a_deck_without_the_example_slides`: 2 slides → form error
    containing « 6 example slides » (English msgid; assert on the French « 6 slides d'exemple » with
    `user.language = "fr"`).
  - `test_report_template_refuses_a_non_pptx_file`: `b"not a zip"` → form error.
- [ ] **Step 4:** Run `python manage.py test team` → FAIL.
- [ ] **Step 5:** Implement `validate_report_template` (open with `pptx.Presentation`, any exception
  or `len(slides) != 6` → `ValidationError`), the field (validators=[validate_report_template],
  verbose name "PowerPoint template", help text "The company deck with its 6 example slides. Stays on
  this PC."), the migration, the form field (`ClearableFileInput`, `accept=".pptx"`), `enctype` on
  the form template and `request.FILES` in the view if missing.
- [ ] **Step 6:** Run `python manage.py test team` → PASS.
- [ ] **Step 7:** Translations fr/nl for the new msgids, `compilemessages`. Commit
  `feat(team): modèle PowerPoint du projet pour le rapport direction`.

### Task 2: Report metrics (pure functions)

**Files:**
- Create: `sprint/report_metrics.py`
- Test: `sprint/tests.py` (new class `ReportMetricsTests`, `SimpleTestCase`)

**Interfaces:**
- Consumes: `build_scope_flow(...)` result (`issues`, `completion`, `totals`, `status_categories`),
  `get_status_histories` shape (`status_changes: [{at, from, to}]`, `created`),
  `KanbanMetricsService().business_hours_between`, `hours_to_business_days`,
  `JiraFox.services.dashboard.AGING_WIP_THRESHOLD_DAYS`.
- Produces:
  - `is_closed(issue: dict, categories: dict) -> bool` and `is_started_status(name, categories) -> bool`
    (same rule as `completion_rates`; refactor `completion_rates` to use them).
  - `cycle_time_days(history: dict, categories: dict, end: datetime) -> float | None`: business days
    from the first entry into a started status to the last entry into a done status ≤ `end`.
  - `percentile(values: list[float], p: int) -> float | None` (linear interpolation; `[]` → None).
  - `sprint_metrics(scope: dict, histories: dict | None, end: datetime | None) -> dict` with keys
    `planned, added, closed, not_started, throughput, cycle_median, cycle_p85, wip_end,
    percent_planned_closed, percent_added`.
  - `kanban_snapshot(scope: dict, histories: dict | None, at: datetime, limit: int = 5) -> dict` with
    `wip, aging, aging_percent, oldest: [{key, summary, status, age_days, url}]`.
  - `age_in_status_days(history, at) -> float | None` and `went_back(history, categories, end) -> bool`
    (a status change from a started/done category to a lower category).

- [ ] **Step 1: Write the failing tests** (`ReportMetricsTests`), with histories built in-test:
  - `test_sprint_metrics_counts_the_planned_and_added_issues`: 3 planned (2 done) + 1 added (1 done) →
    `throughput == 3`, `percent_planned_closed == 67`, `percent_added == 25`, `wip_end == 1`.
  - `test_cycle_time_starts_at_the_first_started_status`: To Do → In Progress Mon 09:00 → Done Wed
    09:00 → `cycle_time_days == 2.0` (8 h days); a status listed in `started_statuses` (e.g.
    "Pending", put in `indeterminate` by `build_scope_flow`) starts the clock too.
  - `test_cycle_time_is_none_without_a_done_transition`.
  - `test_percentile_interpolates`: `[1,2,3,4]`, p=50 → 2.5, p=85 → 3.55; `[]` → None.
  - `test_empty_sprint_gives_gaps_not_zeros`: no issues → `percent_planned_closed is None`,
    `percent_added is None`, `cycle_median is None`.
  - `test_kanban_snapshot_is_rebuilt_at_the_given_date`: an issue Done today but In Progress at `at`
    counts in `wip`; ages measured to `at`; `oldest` sorted by age desc, max `limit`.
  - `test_went_back_detects_a_return_to_to_do`.
- [ ] **Step 2:** Run `python manage.py test sprint.tests.ReportMetricsTests` → FAIL.
- [ ] **Step 3:** Implement. `status_at` from `sprint/logged_report.py` rebuilds the status at `at`.
- [ ] **Step 4:** Run `python manage.py test sprint` → PASS (existing scope tests included).
- [ ] **Step 5:** Commit `feat(sprint): métriques du rapport direction`.

### Task 3: Gathering the data of a set of sprints

**Files:**
- Create: `sprint/report_data.py`
- Modify: `sprint/views.py` (`SprintScopeFlowView` uses `load_sprint_scope`), `jiramodule/services/jira_client.py`
- Test: `sprint/tests.py` (`ReportDataTests`), `jiramodule/tests.py`

**Interfaces:**
- Consumes: Task 2 functions; Jira client `get_sprint`, `list_sprints`, `get_sprint_scope_report`,
  `get_status_histories`, `status_category_keys`, `get_sprint_issue_keys`, `get_issue_url`.
- Produces:
  - `JiraManager.get_descriptions(keys: list[str], limit: int = 500) -> dict[str, str]`
    (`fields="description"`, chunks of `STATUS_HISTORY_CHUNK_SIZE`, text cut to `limit` chars).
  - `load_sprint_scope(jira, sprint: dict) -> dict` with `sprint, start, end, scope, histories,
    warnings: dict` where `warnings` holds the same lists as the scope page context
    (`scope_errors, planned_created_late, done_elsewhere, missing_from_report, missing_check_error,
    without_history, history_error`). The scope page view is refactored onto it (its tests must pass
    unchanged).
  - `closed_sprints(jira) -> list[dict]` (board's closed sprints, sorted by end date).
  - `sprints_in_range(sprints, start: date, end: date) -> list[dict]` (closed sprints whose
    [start, end] overlaps the range).
  - `trend_for(sprint: dict, closed: list[dict], count: int = 5) -> list[dict]` (Ruling: previous 5).
  - `build_report_data(jira, sprints: list[dict], *, focus: dict | None = None) -> dict`:
    `{"title_period", "in_progress": bool, "sprints": [{"sprint", "metrics", "warnings", "scope"}],
    "focus", "snapshot", "totals"}`; `focus` is the single sprint (or the last of the list), the
    snapshot is taken on it at its end. Closed sprints are cached 24 h (Ruling: cache).
  - `sprint_warning_lines(warnings: dict) -> list[str]` — French sentences for speaker notes and the
    multi-sprint page.

- [ ] **Step 1: Write the failing tests** with a `SimpleNamespace` fake Jira like
  `_login_and_fake_jira` (reuse `SCOPE_REPORT`, `SPRINT`):
  - `test_sprints_in_range_keeps_overlapping_closed_sprints` (active/future ignored, edges inclusive).
  - `test_trend_for_takes_the_five_previous_closed_sprints` (7 closed → 5, oldest first; 2 → 2).
  - `test_build_report_data_marks_an_active_sprint_in_progress`.
  - `test_build_report_data_survives_a_history_error`: `get_status_histories` raises `JIRAError` →
    data built, `warnings["history_error"]` set, metrics needing history are None.
  - `test_build_report_data_caches_closed_sprints_only` (second call → no Jira call for the closed one).
  - `jiramodule/tests.py`: `test_get_descriptions_truncates_and_chunks`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement; refactor `SprintScopeFlowView.get_context_data` to call
  `load_sprint_scope` and spread its `warnings` in the context.
- [ ] **Step 4:** Run `python manage.py test` → PASS.
- [ ] **Step 5:** Commit `feat(sprint): données du rapport direction sur un ou plusieurs sprints`.

### Task 4: Analysis providers (Copilot manual, no AI)

**Files:**
- Create: `sprint/analysis.py`
- Test: `sprint/tests.py` (`AnalysisTests`)

**Interfaces:**
- Consumes: `build_report_data` result, `went_back`, `age_in_status_days`, `get_descriptions`.
- Produces:
  - `@dataclass Analysis: situation: list[str]; trend: list[str]; risks: list[Risk]` with
    `@dataclass Risk: title: str; text: str`; `Analysis.to_session() -> dict`,
    `Analysis.from_session(dict) -> Analysis`.
  - `risky_keys(data: dict, limit: int = 10) -> list[str]` (oldest in progress, not started, went back).
  - `build_dossier(data: dict, descriptions: dict[str, str]) -> str`: French plain text; per sprint the
    metrics; per issue of the scope one line `clé | type | titre | assigné | statut début → fin |
    prévu/ajouté | issue | âge dans le statut (j) | revenu en arrière: oui/non`; then risky issues
    with their description. Never comments, never the token.
  - `class AnalysisProvider` (base: `key`, `label`, `needs_prompt: bool`,
    `prompt(dossier: str) -> str`, `parse(text: str) -> Analysis`).
  - `CopilotManualProvider(key="copilot")`, `NoAIProvider(key="none")`,
    `PROVIDERS: dict[str, AnalysisProvider]`, `class AnalysisParseError(ValueError)`.
  - Response format the prompt imposes (exact headings):
    ```
    ## OÙ EN EST L'ÉQUIPE
    - puce
    ## TENDANCE
    - puce
    ## RISQUES
    ### Titre du risque
    Explication.
    ```
    The prompt also says: use only the figures of the dossier, 5 to 7 bullets in total over the two
    first sections, at most 3 risks, French.

- [ ] **Step 1: Write the failing tests**:
  - `test_parse_reads_the_three_sections` (bullets `-`, `*`, `•` accepted; heading case and accents
    tolerant: « Ou en est l'equipe »).
  - `test_parse_keeps_at_most_three_risks`.
  - `test_parse_without_sections_raises_a_clear_error` (message names the missing heading).
  - `test_dossier_has_titles_assignees_and_descriptions_of_risky_issues_only`.
  - `test_dossier_never_contains_the_token` (fake cfg with `jira_token="SECRET"` → not in dossier).
  - `test_analysis_round_trips_through_the_session`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(sprint): fournisseurs d'analyse (Copilot manuel, sans IA)`.

### Task 5: PowerPoint builder

**Files:**
- Create: `sprint/pptx_report.py`
- Test: `sprint/tests.py` (`PptxReportTests`)

**Interfaces:**
- Consumes: `build_report_data` result, `Analysis | None`, `sprint_warning_lines`, template path.
- Produces: `build_deck(data: dict, analysis: Analysis | None, template_path: str | None) -> bytes`.

Deck per the spec table (9 slides, 7 without AI). Template mode: open the template, for each output
slide deep-copy the matching example slide (`spTree` children + image/chart relationships) into a new
slide on the same layout, fill the text frames by **shape name**, add charts with
`shapes.add_chart` (bar/line, native), then delete the 6 original example slides.

| Template example slide (file order) | Shapes to fill | Output slides |
|---|---|---|
| 1 Titre | `Rectangle 3/4/5`, date placeholder | 1 |
| 2 Layout 1 (2 colonnes) | the two `ZoneTexte` + `Rectangle` sub-titles | 6 Instantané Kanban |
| 3 Layout 2 (blocs empilés) | the two `ZoneTexte 58` + sub-titles | 2 Synthèse |
| 4 Layout 3 (panneau rouge) | `Rectangle 18` (red panel), `ZoneTexte 19`; chart in place of `Graphique 4` | 3 Avancement |
| 4 Layout 3, body shapes removed (titles kept) | title + chart / table | 4, 5, 8 |
| 5 Layout 4 (3 lignes) | `Rectangle 6/20/24` (title), sibling text | 7 Risques |
| 6 Merci | — | 9 |

Every example slide has two `Titre 1` shapes (title, « - Sous-titre - »): fill both. Neutral mode (no template or unreadable file): a blank
16:9 `Presentation()` with JiraFox colours (planned `#0d6efd`, added `#ffc107`, done `#198754`).
Each slide's warnings go in its **speaker notes**; the AI slides carry the disclaimer as a last text
line. Title: « Rapport d'équipe », project name, period (`« Sprint 12 (en cours) »` for an active
sprint, else « du 01/10/2026 au 02/11/2026 »), « Direction • 05/11/2026 ».

- [ ] **Step 1: Write the failing tests** (reopen the bytes with `pptx.Presentation(BytesIO(...))`):
  - `test_neutral_deck_has_nine_slides_with_ai_and_seven_without`.
  - `test_charts_are_native` (slides 3, 4, 5 have a shape with `has_chart`; series values equal the
    metrics; a `None` metric is written as an empty point, not 0).
  - `test_ai_text_and_disclaimer_on_slides_2_and_7`.
  - `test_warnings_go_to_the_speaker_notes_not_the_slide`.
  - `test_template_deck_reuses_the_example_slides`: a 6-slide fixture template generated in-test with
    shapes named like the Solidaris ones → output has no « texte de substitution » and the 6 example
    slides are gone.
  - `test_annex_has_no_assignee`.
  - `test_active_sprint_title_says_en_cours`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Manual check: generate with `exports/PowerPoint-Solidaris-FR.pptx` from a shell
  script in the scratchpad, render to PNG (LibreOffice if present) and look at every slide.
- [ ] **Step 6:** Commit `feat(sprint): génération du PowerPoint du rapport direction`.

### Task 6: Pages — multi-sprint view, report page, buttons, menu

**Files:**
- Modify: `sprint/urls.py`, `sprint/views.py`, `templates/sprint/sprint_scope_flow.html`,
  `templates/sprint/sprint_toolbar_actions.html`, `templates/includes/header.html`, `locale/*`
- Create: `templates/sprint/sprint_trends.html`, `templates/sprint/management_report.html`
- Test: `sprint/tests.py` (`ManagementReportViewTests`)

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces:
  - `path("trends/", SprintTrendsView.as_view(), name="sprint-trends")` — GET `start`, `end` (dates) or
    repeated `sprint=<id>`; shows the closed-sprint checklist, the slide 4/5 charts (Chart.js), the
    snapshot, one row per sprint, each sprint's warnings (red alert for `scope_errors`), and the
    « Rapport direction » button (hidden when no sprint is selected, with a « No closed sprint in this
    range » message).
  - `path("management-report/", ManagementReportView.as_view(), name="sprint-management-report")` —
    GET `sprint=<id>` (repeated) and optional `focus=<id>` (single-sprint report: focus + trend):
    provider choice; for Copilot, the prompt and the **full dossier** in a read-only `<textarea>` with a
    « Copy » button, a textarea for the answer. POST `action=parse` → parse, store
    `analysis.to_session()` in `request.session["management_report"]` keyed by the sprint ids, show
    editable fields (bullets one per line, 3 risk title/text pairs); a parse error shows an alert with
    its message and keeps the text. POST `action=generate` → reads the edited fields (or no AI) →
    `build_deck` → `.pptx` download `rapport-direction-<project>-<date>.pptx`.
  - « Rapport direction » button on the scope page and the sprint toolbar
    (`?sprint=<id>&focus=<id>`); « Plusieurs sprints » entry under the Sprint menu.

- [ ] **Step 1: Write the failing tests** (fake Jira as in the scope tests, French UI):
  - `test_trends_page_lists_the_sprints_of_the_range`.
  - `test_trends_page_without_closed_sprint_says_so`.
  - `test_trends_page_shows_scope_errors_in_red`.
  - `test_report_page_shows_the_full_dossier_before_copying`.
  - `test_parse_error_keeps_the_answer_and_explains`.
  - `test_generate_returns_a_pptx` (content type
    `application/vnd.openxmlformats-officedocument.presentationml.presentation`, opens with python-pptx).
  - `test_generate_without_ai_needs_no_answer`.
  - `test_scope_page_links_to_the_report`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** `python manage.py test` → PASS.
- [ ] **Step 5:** fr/nl translations, `compilemessages`. No real Jira is reachable from this PC: check
  both pages rendered by the test client in French (Renaud checks them on real data afterwards).
- [ ] **Step 6:** Commit `feat(sprint): vue multi-sprints et page du rapport direction`, push `main`.
