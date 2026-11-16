"""
A trace of each PowerPoint generation, to look at it again on another PC: under PPTX_LOG_DIR, one folder
per deck holding its inputs (input.json), a copy of the template used and the deck produced, or the error.
`manage.py replay_pptx <folder>` builds the deck again from them. Tracing never breaks the generation.
"""

import json
import logging
import shutil
import traceback
from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.text import slugify

from .analysis import Analysis
from .pptx_report import build_deck

logger = logging.getLogger(__name__)

INPUT = "input.json"
TEMPLATE = "template.pptx"
DECK = "deck.pptx"
ERROR = "error.txt"
LOG = "pptx.log"


def _encode(value):
    # Dates come back as dates on replay; anything else unexpected is kept readable.
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _decode(value: dict):
    if "__datetime__" in value:
        return parse_datetime(value["__datetime__"])
    if "__date__" in value:
        return parse_date(value["__date__"])
    return value


def traced_build_deck(data: dict, analysis: Analysis | None, template_path: str | None, project: str = "") -> bytes:
    """`build_deck`, leaving its inputs and its deck (or error) in a new folder of PPTX_LOG_DIR."""
    folder = _new_folder(project)
    if folder:
        _try(folder, "inputs", lambda: _write_inputs(folder, data, analysis, template_path, project))
    try:
        deck = build_deck(data, analysis, template_path)
    except Exception:
        if folder:
            _try(folder, "error", lambda: (folder / ERROR).write_text(traceback.format_exc(), encoding="utf-8"))
            _log_line(folder, "ERROR")
        raise
    if folder:
        _try(folder, "deck", lambda: (folder / DECK).write_bytes(deck))
        _log_line(folder, f"OK {len(deck)} bytes")
    return deck


def load_trace(folder: Path) -> tuple[dict, Analysis | None, str | None]:
    """(data, analysis, template path) of a traced generation, as `build_deck` takes them."""
    stored = json.loads((folder / INPUT).read_text(encoding="utf-8"), object_hook=_decode)
    analysis = Analysis.from_session(stored["analysis"]) if stored["analysis"] is not None else None
    template = folder / TEMPLATE
    return stored["data"], analysis, str(template) if template.exists() else None


def _new_folder(project: str) -> Path | None:
    root = Path(settings.PPTX_LOG_DIR)
    name = f"{timezone.localtime():%Y%m%d-%H%M%S}-{slugify(project) or 'sprint'}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        folder = root / name
        suffix = 1
        while folder.exists():
            suffix += 1
            folder = root / f"{name}-{suffix}"
        folder.mkdir()
        _prune(root)
    except OSError:
        logger.exception("PowerPoint trace: cannot create a folder in %s", root)
        return None
    return folder


def _prune(root: Path) -> None:
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    for old in folders[:-settings.PPTX_LOG_KEEP]:
        shutil.rmtree(old, ignore_errors=True)


def _write_inputs(folder: Path, data, analysis, template_path, project) -> None:
    stored = {
        "created": timezone.localtime().isoformat(),
        "project": project,
        "template": Path(template_path).name if template_path else None,
        "analysis": analysis.to_session() if analysis is not None else None,
        "data": data,
    }
    (folder / INPUT).write_text(json.dumps(stored, default=_encode, ensure_ascii=False, indent=1), encoding="utf-8")
    if template_path and Path(template_path).exists():
        shutil.copyfile(template_path, folder / TEMPLATE)


def _try(folder: Path, what: str, write) -> None:
    try:
        write()
    except Exception:  # noqa: BLE001 - the trace is a help, never a reason to fail the report.
        logger.exception("PowerPoint trace: cannot write the %s in %s", what, folder)


def _log_line(folder: Path, outcome: str) -> None:
    def append():
        with (folder.parent / LOG).open("a", encoding="utf-8") as log:
            log.write(f"{timezone.localtime():%Y-%m-%d %H:%M:%S} {folder.name} {outcome}\n")

    _try(folder, "log line", append)
