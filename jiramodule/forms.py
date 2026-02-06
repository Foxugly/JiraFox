from django import forms
from .models import JiraConfiguration


class JiraConfigurationForm(forms.ModelForm):
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
            "jira_token": forms.TextInput(attrs={
                "class": "form-control",
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