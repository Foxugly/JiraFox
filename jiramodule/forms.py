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
        fields = [
            "jira_url",
            "jira_email",
            "jira_token",
            "jira_board_id",
            "jira_project_id",
            "jira_recurrent_epic_key",
            "current",
        ]
        widgets = {
            "jira_url": forms.URLInput(attrs={
                "class": "form-control",
                "placeholder": "https://jira.example.com",
            }),
            "jira_email": forms.EmailInput(attrs={
                "class": "form-control",
                "placeholder": "user@company.com",
            }),
            "jira_token": forms.PasswordInput(render_value=False, attrs={
                "class": "form-control",
                "autocomplete": "new-password",
            }),
            "jira_board_id": forms.NumberInput(attrs={
                "class": "form-control",
            }),
            "jira_project_id": forms.NumberInput(attrs={
                "class": "form-control",
            }),
            "jira_recurrent_epic_key": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "ABC-123",
            }),
            # 🔥 SWITCH Bootstrap 5
            "current": forms.CheckboxInput(attrs={
                "class": "form-check-input",
                "role": "switch",
            }),
        }