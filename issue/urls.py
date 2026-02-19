from django.urls import path
from .views import IssueDetailView

urlpatterns = [
    path("<str:jira_key>/", IssueDetailView.as_view(), name="issue-detail"),

]