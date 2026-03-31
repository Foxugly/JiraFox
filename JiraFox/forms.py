from django import forms
from django.conf import settings


class UserSettingsForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=True)
    language = forms.ChoiceField(choices=settings.LANGUAGES)

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
