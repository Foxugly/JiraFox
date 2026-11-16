from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import translation

from sprint.pptx_log import INPUT, load_trace
from sprint.pptx_report import build_deck


class Command(BaseCommand):
    help = "Build again the PowerPoint of a trace folder (logs/pptx/<folder>, see sprint/pptx_log.py)."

    def add_arguments(self, parser):
        parser.add_argument("folder", help="A trace folder, copied from the PC that generated the deck.")
        parser.add_argument("-o", "--output", help="Deck to write (default: <folder>/replay.pptx).")

    def handle(self, *args, folder, output=None, **options):
        folder = Path(folder)
        if not (folder / INPUT).exists():
            raise CommandError(f"No {INPUT} in {folder}.")
        data, analysis, template = load_trace(folder)
        with translation.override("fr"):
            deck = build_deck(data, analysis, template)
        target = Path(output) if output else folder / "replay.pptx"
        target.write_bytes(deck)
        self.stdout.write(f"{target} ({len(deck)} bytes, template: {template or 'neutral'})")
