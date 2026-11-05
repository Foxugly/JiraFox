from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# The company deck must hold exactly the example slides the report is built from.
TEMPLATE_SLIDE_COUNT = 6


def validate_report_template(file) -> None:
    """Raise ValidationError unless `file` is a PowerPoint deck with the 6 example slides.

    Only fresh uploads are checked: a stored file (already committed) is not re-read on every save.
    """
    if getattr(file, "_committed", False):
        return
    from pptx import Presentation

    try:
        slide_count = len(Presentation(file).slides)
    except Exception as exc:  # python-pptx raises many types on a corrupt file
        raise ValidationError(_("This file is not a readable PowerPoint (.pptx) deck.")) from exc
    if slide_count != TEMPLATE_SLIDE_COUNT:
        raise ValidationError(_("The deck must contain the 6 example slides."))
    file.seek(0)
