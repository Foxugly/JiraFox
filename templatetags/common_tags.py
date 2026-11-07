from decimal import Decimal

from django import template

register = template.Library()

@register.filter
def format_assignee(assignee):
    """
    Transforme un dict Jira contenant:
    - displayName : "Nom, Prénom"
    - name : identifiant (ex: TDPTSH)

    En une chaîne : "Prénom Nom (TDPTSH)"
    """

    if not isinstance(assignee, dict):
        return ""

    display = assignee.get("displayName")  # "Henno, Stéphane"
    username = assignee.get("name")        # "TDPTSH"

    if not display:
        return username or ""

    # Si displayName est du style "Nom, Prénom"
    if "," in display:
        last, first = [x.strip() for x in display.split(",", 1)]
        full = f"{first} {last}"
    else:
        full = display

    # Ajout du username si présent
    if username:
        return f"{full} ({username})"

    return full


@register.filter
def hours_input(value):
    """Hours as an <input type="number"> value: a decimal point and no trailing zeros (3.60 -> "3.6", 36.00 -> "36")."""
    if value is None or value == "":
        return ""
    return format(Decimal(value).normalize(), "f")
