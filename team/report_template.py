from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# The company deck must hold exactly the example slides the report is built from.
TEMPLATE_SLIDE_COUNT = 6


def validate_report_template(file) -> None:
    """Raise ValidationError unless `file` is a PowerPoint deck with the 6 example slides."""
    from pptx import Presentation

    try:
        file.seek(0)
        slide_count = len(Presentation(file).slides)
    except Exception as exc:  # python-pptx raises many types on a corrupt file
        raise ValidationError(_("This file is not a readable PowerPoint (.pptx) deck.")) from exc
    finally:
        file.seek(0)
    if slide_count != TEMPLATE_SLIDE_COUNT:
        raise ValidationError(_("The deck must contain the 6 example slides."))
