"""
The management report as a PowerPoint deck (French, hard-coded). The company template's example
slides are cloned, filled by shape name, then removed. Without a usable template a neutral one is
built in code with the same shape names, so both paths share the filling code.
"""

from collections import Counter
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from django.utils import timezone
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from JiraFox.services.dashboard import AGING_WIP_THRESHOLD_DAYS

from .logged_report import sprint_window
from .report_data import sprint_warning_lines
from .report_metrics import is_closed

DISCLAIMER = "Texte généré par IA à partir des métriques JiraFox — à relire avant diffusion"
BLUE, YELLOW, GREEN, GREY, ORANGE, DARK = "0d6efd", "ffc107", "198754", "6c757d", "fd7e14", "212529"
TITLE_EXAMPLE, COLUMNS_EXAMPLE, STACKED_EXAMPLE, PANEL_EXAMPLE, ROWS_EXAMPLE, END_EXAMPLE = range(6)
TITLE = "Titre 1"
RISK_ROWS = (("Rectangle 4", "Rectangle 6"), ("Rectangle 18", "Rectangle 20"), ("Rectangle 22", "Rectangle 24"))
# The named shapes each example slide must hold (a name listed twice is needed twice).
EXPECTED_SHAPES = {
    TITLE_EXAMPLE: ("Rectangle 4", "Rectangle 5", "Rectangle 3", "Espace réservé de la date 3"),
    COLUMNS_EXAMPLE: (TITLE, TITLE, "Rectangle 31", "ZoneTexte 29", "Rectangle 58", "ZoneTexte 55"),
    STACKED_EXAMPLE: (TITLE, TITLE, "Rectangle 24", "ZoneTexte 58", "Rectangle 16", "ZoneTexte 58"),
    PANEL_EXAMPLE: (TITLE, TITLE, "Rectangle 14", "ZoneTexte 58", "Rectangle 21", "Rectangle 18", "ZoneTexte 19"),
    ROWS_EXAMPLE: (TITLE, TITLE) + tuple(name for row in RISK_ROWS for name in row),
}
ANNEX_ROWS = 12
SYNTHESIS_BULLETS = 7
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# Boxes as fractions of the slide (left, top, width, height), measured on the company template.
BODY = (0.074, 0.31, 0.853, 0.64)
PANEL_CHART = (0.058, 0.525, 0.53, 0.44)
DISCLAIMER_BOX = (0.074, 0.935, 0.853, 0.045)


def _box(prs, box):
    left, top, width, height = box
    return [Emu(int(size * fraction)) for size, fraction in zip(
        (prs.slide_width, prs.slide_height) * 2, (left, top, width, height)
    )]


def _number(value) -> str:
    if value is None:
        return "-"
    return f"{value:g}".replace(".", ",") if isinstance(value, float) else str(value)


def _focus(data: dict) -> dict:
    focus_id = (data.get("focus") or {}).get("id")
    return next((entry for entry in data["sprints"] if entry["sprint"]["id"] == focus_id), data["sprints"][-1])


def _name(entry: dict) -> str:
    return entry["sprint"].get("name") or f"Sprint {entry['sprint']['id']}"


# --- Shapes -------------------------------------------------------------------------------------


def _find(shapes, name: str) -> list:
    """Shapes called `name`, inside groups too, top first."""
    found = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            found.extend(_find(shape.shapes, name))
        elif shape.name == name:
            found.append(shape)
    return sorted(found, key=lambda shape: shape.top or 0)


def _fill(slide, name: str, lines: list[str], index: int = 0, *, wrap: bool | None = None):
    """Replace the text of the `index`-th shape `name`, keeping each paragraph's formatting."""
    shapes = _find(slide.shapes, name)
    if len(shapes) <= index:
        return
    if wrap is not None:
        shapes[index].text_frame.word_wrap = wrap
    body = shapes[index].text_frame._txBody
    models = body.findall(qn("a:p"))
    for paragraph in models:
        body.remove(paragraph)
    for number, line in enumerate(lines or [""]):
        paragraph = deepcopy(models[min(number, len(models) - 1)])
        runs = paragraph.findall(qn("a:r"))
        for child in list(paragraph):
            if child.tag in (qn("a:r"), qn("a:br"), qn("a:fld")) and (not runs or child is not runs[0]):
                paragraph.remove(child)
        if runs:
            runs[0].find(qn("a:t")).text = line
        else:
            run = paragraph.makeelement(qn("a:r"), {})
            end = paragraph.find(qn("a:endParaRPr"))
            if end is not None:
                run.append(deepcopy(end))
                run[0].tag = qn("a:rPr")
            text = run.makeelement(qn("a:t"), {})
            text.text = line
            run.append(text)
            paragraph.insert(len(paragraph) - (end is not None), run)
        body.append(paragraph)


def _remove(slide, name: str):
    """Remove the shapes `name` with the group that holds them, so no empty frame is left."""
    tree = slide.shapes._spTree
    for shape in _find(slide.shapes, name):
        element = shape._element
        while element.getparent() is not None and element.getparent() is not tree:
            element = element.getparent()
        if element.getparent() is tree:
            tree.remove(element)


def _add_text(prs, slide, name, box, lines, *, size=14, bold=False, italic=False, colour=DARK, align=None):
    shape = slide.shapes.add_textbox(*_box(prs, box))
    shape.name = name
    frame = shape.text_frame
    frame.word_wrap = True
    for number, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
        if align:
            paragraph.alignment = align
        run = paragraph.add_run()
        run.text = line
        run.font.size, run.font.bold, run.font.italic = Pt(size), bold, italic
        run.font.color.rgb = RGBColor.from_string(colour)
    return shape


def _notes(slide, lines: list[str]):
    if lines:
        slide.notes_slide.notes_text_frame.text = "\n".join(lines)


def _chart(prs, slide, kind, categories, series, box, title=None, number_format="0"):
    data = CategoryChartData(number_format=number_format)
    data.categories = categories
    for name, values, _ in series:
        data.add_series(name, values)
    chart = slide.shapes.add_chart(kind, *_box(prs, box), data).chart
    chart.has_title = bool(title)
    if title:
        chart.chart_title.text_frame.text = title
        chart.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    chart.has_legend, chart.legend.position, chart.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
    chart.font.size = Pt(11)
    plot = chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.number_format, plot.data_labels.number_format_is_linked = number_format, False
    for drawn, (_, _, colour) in zip(plot.series, series):
        rgb = RGBColor.from_string(colour)
        if kind == XL_CHART_TYPE.LINE_MARKERS:
            drawn.format.line.color.rgb = rgb
            drawn.marker.format.fill.solid()
            drawn.marker.format.fill.fore_color.rgb = rgb
            drawn.smooth = False
        else:
            drawn.format.fill.solid()
            drawn.format.fill.fore_color.rgb = rgb
    return chart


# --- Template -----------------------------------------------------------------------------------


def _copy(source, slide, element, rids: dict):
    """Deep copy of `element` whose relationship ids point to the same parts from `slide`."""
    copy = deepcopy(element)
    for node in copy.iter():
        for attr, value in node.attrib.items():
            if attr.startswith(R_NS) and value in source.part.rels:
                if value not in rids:
                    rel = source.part.rels[value]
                    target = rel.target_ref if rel.is_external else rel.target_part
                    rids[value] = slide.part.relate_to(target, rel.reltype, rel.is_external)
                node.set(attr, rids[value])
    return copy


def _clone(prs, source, *, titles_only: bool = False):
    """A new slide on the layout of `source` holding a copy of its background and shapes."""
    slide = prs.slides.add_slide(source.slide_layout)
    tree = slide.shapes._spTree
    for shape in list(slide.shapes):
        tree.remove(shape._element)
    rids = {}
    background = source.element.cSld.bg
    if background is not None:
        slide.element.cSld.insert(0, _copy(source, slide, background, rids))
    skipped = (qn("p:nvGrpSpPr"), qn("p:grpSpPr"), qn("p:extLst"))
    for element in source.shapes._spTree.iterchildren():
        if element.tag in skipped:
            continue
        name = element.find(f".//{qn('p:cNvPr')}")
        if titles_only and (name is None or name.get("name") != TITLE):
            continue
        tree.insert_element_before(_copy(source, slide, element, rids), "p:extLst")
    return slide


def _drop(prs, slide):
    slide_ids = prs.slides._sldIdLst
    for slide_id in list(slide_ids):
        if prs.part.related_part(slide_id.rId) is slide.part:
            prs.part.rels.pop(slide_id.rId)
            slide_ids.remove(slide_id)


def _neutral_template():
    """Six example slides named like the company ones, in JiraFox colours on white."""
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(12192000), Emu(6858000)
    blank = prs.slide_layouts[6]

    def titled():
        slide = prs.slides.add_slide(blank)
        _add_text(prs, slide, TITLE, (0.074, 0.08, 0.853, 0.09), [""], size=30, bold=True, align=PP_ALIGN.CENTER)
        _add_text(prs, slide, TITLE, (0.074, 0.18, 0.853, 0.06), [""], size=16, colour=GREY, align=PP_ALIGN.CENTER)
        return slide

    def heading(slide, name, box, colour=BLUE):
        _add_text(prs, slide, name, box, [""], size=16, bold=True, colour=colour)

    slide = prs.slides.add_slide(blank)
    band = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Emu(int(prs.slide_height * 0.04)))
    band.fill.solid()
    band.fill.fore_color.rgb = RGBColor.from_string(BLUE)
    band.line.fill.background()
    _add_text(prs, slide, "Rectangle 4", (0.074, 0.30, 0.853, 0.12), [""], size=40, bold=True, colour=BLUE)
    _add_text(prs, slide, "Rectangle 5", (0.074, 0.43, 0.853, 0.08), [""], size=26)
    _add_text(prs, slide, "Rectangle 3", (0.074, 0.52, 0.853, 0.07), [""], size=18, colour=GREY)
    _add_text(prs, slide, "Espace réservé de la date 3", (0.074, 0.90, 0.5, 0.05), [""], size=12, colour=GREY)

    slide = titled()
    for name, text, left in (("Rectangle 31", "ZoneTexte 29", 0.074), ("Rectangle 58", "ZoneTexte 55", 0.52)):
        heading(slide, name, (left, 0.31, 0.40, 0.06))
        _add_text(prs, slide, text, (left, 0.38, 0.40, 0.55), [""])

    slide = titled()
    for name, top in (("Rectangle 24", 0.31), ("Rectangle 16", 0.62)):
        heading(slide, name, (0.074, top, 0.853, 0.05))
        _add_text(prs, slide, "ZoneTexte 58", (0.074, top + 0.06, 0.853, 0.23), [""])

    slide = titled()
    heading(slide, "Rectangle 14", (0.058, 0.465, 0.53, 0.05), colour=DARK)
    _add_text(prs, slide, "ZoneTexte 58", (0.058, 0.53, 0.53, 0.40), [""])
    panel = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, *_box(prs, (0.66, 0.31, 0.29, 0.64)))
    panel.name = "Graphique 4"
    panel.fill.solid()
    panel.fill.fore_color.rgb = RGBColor.from_string(BLUE)
    panel.line.fill.background()
    _add_text(prs, slide, "Rectangle 21", (0.68, 0.36, 0.25, 0.12), [""], size=36, bold=True, colour="ffffff",
              align=PP_ALIGN.CENTER)
    _add_text(prs, slide, "Rectangle 18", (0.68, 0.49, 0.25, 0.06), [""], size=14, bold=True, colour="ffffff",
              align=PP_ALIGN.CENTER)
    _add_text(prs, slide, "ZoneTexte 19", (0.68, 0.57, 0.25, 0.34), [""], colour="ffffff", align=PP_ALIGN.CENTER)

    slide = titled()
    for number, (label, text) in enumerate(RISK_ROWS):
        top = 0.31 + 0.21 * number
        heading(slide, label, (0.074, top, 0.15, 0.18))
        shape = _add_text(prs, slide, text, (0.24, top, 0.687, 0.18), ["", ""])
        shape.text_frame.paragraphs[0].runs[0].font.bold = True

    slide = prs.slides.add_slide(blank)
    _add_text(prs, slide, "Rectangle 3", (0.074, 0.42, 0.853, 0.14), ["Merci."], size=40, bold=True, colour=BLUE,
              align=PP_ALIGN.CENTER)
    return prs


def _open_template(path: str | None):
    """(presentation, notice for slide 1's notes): the neutral one when the template is unusable."""
    if not path:
        return _neutral_template(), None
    try:
        prs = Presentation(path)
    except Exception:  # noqa: BLE001 - whatever is wrong with the file, the report still comes out.
        return _neutral_template(), (
            f"Modèle PowerPoint « {Path(path).name} » introuvable ou illisible : mise en page neutre utilisée."
        )
    if len(prs.slides) < 6:
        return _neutral_template(), (
            f"Le modèle PowerPoint « {Path(path).name} » n'a pas les 6 slides d'exemple : mise en page neutre utilisée."
        )
    return prs, None


def _missing_shapes(examples) -> list[str]:
    """One line per named shape the template lacks: its text cannot be filled."""
    lines = []
    for number, names in EXPECTED_SHAPES.items():
        for name, needed in Counter(names).items():
            if len(_find(examples[number].shapes, name)) < needed:
                lines.append(f"Slide d'exemple {number + 1} du modèle : forme « {name} » absente, texte non rempli.")
    return lines


# --- Slides -------------------------------------------------------------------------------------


def _titled(prs, examples, number, title, subtitle, *, titles_only=False):
    slide = _clone(prs, examples[number], titles_only=titles_only)
    _fill(slide, TITLE, [title], 0)
    # The template's frames are narrow, sized for its placeholder words: let the text overflow them.
    _fill(slide, TITLE, [f"- {subtitle} -"], 1, wrap=False)
    return slide


def _title_slide(prs, examples, data, notices):
    slide = _clone(prs, examples[TITLE_EXAMPLE])
    _fill(slide, "Rectangle 4", ["Rapport d'équipe"])
    _fill(slide, "Rectangle 5", [data.get("project_name") or "-"])
    _fill(slide, "Rectangle 3", [data["title_period"]])
    _fill(slide, "Espace réservé de la date 3", [f"Direction • {timezone.localdate():%d/%m/%Y}"])
    _notes(slide, notices)


def _ai_slide_end(prs, slide):
    _add_text(prs, slide, "Avertissement IA", DISCLAIMER_BOX, [DISCLAIMER], size=10, italic=True, colour=GREY)


def _synthesis_slide(prs, examples, data, analysis):
    slide = _titled(prs, examples, STACKED_EXAMPLE, "Synthèse", data["title_period"])
    # The two frames hold 7 bullets at most: the first ones are kept, in order, the rest go to the notes.
    situation = analysis.situation[:SYNTHESIS_BULLETS]
    trend = analysis.trend[:SYNTHESIS_BULLETS - len(situation)]
    cut = analysis.situation[len(situation):] + analysis.trend[len(trend):]
    _fill(slide, "Rectangle 24", ["OÙ EN EST L'ÉQUIPE"], wrap=False)
    _fill(slide, "ZoneTexte 58", [f"• {line}" for line in situation] or ["-"], 0)
    _fill(slide, "Rectangle 16", ["TENDANCE"], wrap=False)
    _fill(slide, "ZoneTexte 58", [f"• {line}" for line in trend] or ["-"], 1)
    _ai_slide_end(prs, slide)
    if cut:
        _notes(slide, [f"{len(cut)} puce(s) au-delà de {SYNTHESIS_BULLETS} non reprise(s) sur la slide :"] + [
            f"• {line}" for line in cut
        ])


def _single(data) -> bool:
    return bool(data.get("single")) or len(data["sprints"]) == 1


def _progress_slide(prs, examples, data):
    """The focus sprint of a single report, else the sums over the period's sprints."""
    metrics = data["totals"]
    if _single(data):
        label = _name(_focus(data))
        heading, notes = "TICKETS DU SPRINT", sprint_warning_lines(_focus(data)["warnings"])
    else:
        label = f"{len(data['sprints'])} sprints, {data['title_period']}"
        heading, notes = "TICKETS DE LA PÉRIODE", _all_warnings(data)
    slide = _titled(prs, examples, PANEL_EXAMPLE, "Avancement", label)
    _fill(slide, "Rectangle 14", [heading])
    _remove(slide, "ZoneTexte 58")
    series = [
        ("Prévus", [metrics["planned"]], BLUE), ("Ajoutés", [metrics["added"]], YELLOW),
        ("Clôturés", [metrics["closed"]], GREEN), ("Non commencés", [metrics["not_started"]], GREY),
    ]
    _chart(prs, slide, XL_CHART_TYPE.COLUMN_CLUSTERED, [label], series, PANEL_CHART)
    percent = metrics["percent_planned_closed"]
    _fill(slide, "Rectangle 21", [f"{percent}\u00a0%" if percent is not None else "-"], wrap=False)
    _fill(slide, "Rectangle 18", ["des prévus clôturés"], wrap=False)
    _fill(slide, "ZoneTexte 19", [
        f"{metrics['planned']} prévus", f"{metrics['added']} ajoutés", f"{metrics['closed']} clôturés",
        f"{metrics['not_started']} non commencés",
    ])
    if percent is None:
        notes.append("Aucun ticket prévu : le pourcentage des prévus clôturés n'est pas calculable.")
    _notes(slide, notes)


def _all_warnings(data) -> list[str]:
    return [f"{_name(entry)} : {line}" for entry in data["sprints"] for line in sprint_warning_lines(entry["warnings"])]


def _gaps(data, keys: tuple, reason: str) -> list[str]:
    return [f"{_name(entry)} : {reason}" for entry in data["sprints"] if any(entry["metrics"][key] is None for key in keys)]


def _flow_slide(prs, examples, data):
    slide = _titled(prs, examples, PANEL_EXAMPLE, "Flux", "Débit, temps de cycle et WIP par sprint", titles_only=True)
    names = [_name(entry) for entry in data["sprints"]]
    values = {key: [entry["metrics"][key] for entry in data["sprints"]] for key in data["sprints"][0]["metrics"]}
    left, top, width, height = BODY
    half = (width - 0.02) / 2
    _chart(prs, slide, XL_CHART_TYPE.COLUMN_CLUSTERED, names, [
        ("Débit", values["throughput"], GREEN), ("WIP en fin de sprint", values["wip_end"], GREY),
    ], (left, top, half, height), "Débit et WIP en fin de sprint (tickets)")
    _chart(prs, slide, XL_CHART_TYPE.LINE_MARKERS, names, [
        ("Temps de cycle médian", values["cycle_median"], BLUE), ("85e percentile", values["cycle_p85"], ORANGE),
    ], (left + half + 0.02, top, half, height), "Temps de cycle (jours ouvrés)", "0.0")
    _notes(slide, _all_warnings(data) + _gaps(
        data, ("cycle_median",), "temps de cycle non calculable (aucun ticket clôturé avec historique)."
    ))


def _predictability_slide(prs, examples, data):
    slide = _titled(prs, examples, PANEL_EXAMPLE, "Prévisibilité", "Par sprint", titles_only=True)
    _chart(prs, slide, XL_CHART_TYPE.LINE_MARKERS, [_name(entry) for entry in data["sprints"]], [
        ("% des prévus clôturés", [entry["metrics"]["percent_planned_closed"] for entry in data["sprints"]], GREEN),
        ("% ajoutés", [entry["metrics"]["percent_added"] for entry in data["sprints"]], YELLOW),
    ], BODY, "% des prévus clôturés et % de tickets ajoutés en cours de sprint", '0" %"')
    _notes(slide, _all_warnings(data) + _gaps(
        data, ("percent_planned_closed",), "aucun ticket prévu, % des prévus clôturés non calculable."
    ) + _gaps(data, ("percent_added",), "aucun ticket dans le sprint, % ajoutés non calculable."))


def _kanban_slide(prs, examples, data):
    entry = _focus(data)
    end = sprint_window(entry["sprint"], timezone.now())[1]
    slide = _titled(prs, examples, COLUMNS_EXAMPLE, "Instantané Kanban", f"au {end:%d/%m/%Y}")
    _fill(slide, "Rectangle 31", ["WIP ET VIEILLISSEMENT"], wrap=False)
    _fill(slide, "Rectangle 58", ["LES PLUS ANCIENS"], wrap=False)
    snapshot = data.get("snapshot")
    notes = sprint_warning_lines(entry["warnings"])
    if snapshot is None:
        _fill(slide, "ZoneTexte 29", ["WIP : -", "Vieillissants : -"])
        _fill(slide, "ZoneTexte 55", ["-"])
        notes.insert(0, f"Instantané indisponible : pas d'historique des statuts pour {_name(entry)}.")
    else:
        aging = snapshot["aging_percent"]
        _fill(slide, "ZoneTexte 29", [
            f"WIP : {snapshot['wip']} tickets en cours",
            f"Vieillissants (plus de {AGING_WIP_THRESHOLD_DAYS} jours ouvrés) : {snapshot['aging']}"
            f" ({_number(aging)}{'\u00a0%' if aging is not None else ''})",
        ])
        _fill(slide, "ZoneTexte 55", [
            f"{item['key']} — {item['summary']} ({item['status']}, {_number(round(item['age_days'], 1))} j)"
            for item in snapshot["oldest"]
        ] or ["Aucun ticket en cours"])
    _notes(slide, notes)


def _risks_slide(prs, examples, data, analysis):
    slide = _titled(prs, examples, ROWS_EXAMPLE, "Risques", data["title_period"])
    risks = analysis.risks[:len(RISK_ROWS)]
    for number, (label, text) in enumerate(RISK_ROWS):
        if number < len(risks):
            _fill(slide, label, [f"Risque {number + 1}"])
            _fill(slide, text, [risks[number].title, risks[number].text])
        else:
            _remove(slide, label)
            _remove(slide, text)
    _ai_slide_end(prs, slide)


def _annex_slides(prs, examples, data):
    entries = [_focus(data)] if _single(data) else data["sprints"]
    several = len(entries) > 1
    rows = []
    for entry in entries:
        # An issue removed from the sprint is neither closed nor left unfinished by the team.
        issues = [issue for issue in entry["scope"]["issues"] if issue["outcome"] != "removed"]
        closed = [issue for issue in issues if is_closed(issue, entry["categories"])]
        for issue in closed + [issue for issue in issues if issue not in closed]:
            rows.append(([_name(entry)] if several else []) + [issue["key"], issue["summary"], issue["status_end"]])
    header = (["Sprint"] if several else []) + ["Clé", "Titre", "Statut de fin"]
    chunks = [rows[start:start + ANNEX_ROWS] for start in range(0, len(rows), ANNEX_ROWS)] or [[]]
    for number, chunk in enumerate(chunks):
        title = "Annexe" if number == 0 else "Annexe (suite)"
        slide = _titled(prs, examples, PANEL_EXAMPLE, title, "Tickets clôturés et non terminés", titles_only=True)
        if not chunk:
            _add_text(prs, slide, "Annexe vide", BODY, ["Aucun ticket dans le périmètre."])
            continue
        left, top, width, height = _box(prs, BODY)
        table = slide.shapes.add_table(len(chunk) + 1, len(header), left, top, width, Emu(300000 * (len(chunk) + 1))).table
        widths = ([0.16] if several else []) + [0.14, 0.52 if several else 0.64, 0.18]
        for column, share in enumerate(widths):
            table.columns[column].width = Emu(int(width * share / sum(widths)))
        for row, values in enumerate([header] + chunk):
            for column, value in enumerate(values):
                cell = table.cell(row, column)
                cell.text = str(value or "-")
                for run in cell.text_frame.paragraphs[0].runs:
                    run.font.size = Pt(11)


def build_deck(data: dict, analysis, template_path: str | None) -> bytes:
    """
    The report deck: 9 slides, 7 without the AI analysis (more when the annex spans several). Without
    any risk the risks slide is left out rather than claiming there is none.
    """
    prs, notice = _open_template(template_path)
    originals = list(prs.slides)
    examples = originals[:6]
    _title_slide(prs, examples, data, [notice] if notice else _missing_shapes(examples))
    if analysis is not None:
        _synthesis_slide(prs, examples, data, analysis)
    _progress_slide(prs, examples, data)
    _flow_slide(prs, examples, data)
    _predictability_slide(prs, examples, data)
    _kanban_slide(prs, examples, data)
    if analysis is not None and analysis.risks:
        _risks_slide(prs, examples, data, analysis)
    _annex_slides(prs, examples, data)
    _clone(prs, examples[END_EXAMPLE])
    for slide in originals:
        _drop(prs, slide)
    output = BytesIO()
    prs.save(output)
    return output.getvalue()
