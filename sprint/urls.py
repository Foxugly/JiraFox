from django.urls import path
from .views import SprintListView, SprintDetailView, SprintKanbanView, SprintReportView

urlpatterns = [
    path("list/", SprintListView.as_view(), name="sprint-list"),
    path("<int:sprint_id>/", SprintDetailView.as_view(), name="sprint-detail"),
    path("<int:sprint_id>/kanban/", SprintKanbanView.as_view(), name="sprint-kanban"),
    path("<int:sprint_id>/report/", SprintReportView.as_view(), name="sprint-report"),

]