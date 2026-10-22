from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _


LANGUAGE_CHOICES = [
    ("fr", _("French")),
    ("nl", _("Dutch")),
    ("en", _("English")),
]


class UserSettingsForm(forms.Form):
    first_name = forms.CharField(label=_("First name"), max_length=150, required=False)
    last_name = forms.CharField(label=_("Last name"), max_length=150, required=False)
    email = forms.EmailField(label=_("Email"), required=True)
    language = forms.ChoiceField(label=_("Language"), choices=LANGUAGE_CHOICES)

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        initial = kwargs.setdefault("initial", {})
        if user is not None and not args:
            initial.setdefault("first_name", user.first_name)
            initial.setdefault("last_name", user.last_name)
            initial.setdefault("email", user.email)
            initial.setdefault("language", getattr(user, "language", settings.LANGUAGE_CODE))
        super().__init__(*args, **kwargs)

    def save(self):
        user = self.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        user.language = self.cleaned_data["language"]
        user.save(update_fields=["first_name", "last_name", "email", "language"])
        return user
