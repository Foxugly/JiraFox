from django import forms
from django.utils.translation import gettext_lazy as _

from .models import JiraConfiguration


class JiraConfigurationForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.jira_token:
            self.fields["jira_token"].help_text = _("Leave empty to keep the current token.")

    def clean_jira_token(self):
        token = (self.cleaned_data.get("jira_token") or "").strip()
        # The token is never sent back to the browser, so an empty field on edit means "unchanged".
        if not token and self.instance.pk:
            return self.instance.jira_token
        return token

    class Meta:
        model = JiraConfiguration
        fields = ["jira_url", "jira_token"]
        widgets = {
            "jira_url": forms.URLInput(attrs={
                "class": "form-control",
                "placeholder": "https://jira.example.com",
            }),
            "jira_token": forms.PasswordInput(render_value=False, attrs={
                "class": "form-control",
                "autocomplete": "new-password",
            }),
        }
