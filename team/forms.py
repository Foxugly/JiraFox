from django import forms

from .models import Team


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name", "jira_url", "jira_board_id", "jira_project_id", "jira_recurrent_epic_key"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "ASSU POPU"}),
            "jira_url": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://jira.example.com"}),
            "jira_board_id": forms.NumberInput(attrs={"class": "form-control"}),
            "jira_project_id": forms.NumberInput(attrs={"class": "form-control"}),
            "jira_recurrent_epic_key": forms.TextInput(attrs={"class": "form-control", "placeholder": "ABC-123"}),
        }
