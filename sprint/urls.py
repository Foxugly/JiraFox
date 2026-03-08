from django.urls import path
from .views import SprintListView, SprintDetailView, SprintKanbanView, SprintReportView, SprintXLSExportView, \
    get_api_sprint_kanban_issues, SprintWiaView, get_api_sprint_wia_issues, get_api_sprint_issues, \
    SprintDetailXLSExportView

urlpatterns = [
    path("list/", SprintListView.as_view(), name="sprint-list"),
    path("<int:sprint_id>/", SprintDetailView.as_view(), name="sprint-detail"),
    path("<int:sprint_id>/api/issues/", get_api_sprint_issues, name="sprint-api-issues"),
    path("<int:sprint_id>/kanban/", SprintKanbanView.as_view(), name="sprint-kanban"),

    path("<int:sprint_id>/api/kanban/", get_api_sprint_kanban_issues, name="sprint-api-kanban"),
    path("<int:sprint_id>/wia/", SprintWiaView.as_view(), name="sprint-wia"),

    path("<int:sprint_id>/api/wia/", get_api_sprint_wia_issues, name="sprint-api-wia"),
    path("<int:sprint_id>/report/", SprintReportView.as_view(), name="sprint-report"),
    path("<int:sprint_id>/exportXLSX/", SprintXLSExportView.as_view(), name="sprint-xls-export"),
    path("<int:sprint_id>/reportXLSX/", SprintDetailXLSExportView.as_view(), name="sprint_xls_report"),

]