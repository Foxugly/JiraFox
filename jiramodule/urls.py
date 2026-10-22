from django.urls import path
from . import views

urlpatterns = [
    path("bulk/", views.bulk_operations_home, name="jira_bulk_operations"),
    path("bulk/labels/", views.bulk_labels_page, name="jira_bulk_labels"),
    path("bulk/labels/preview/", views.bulk_labels_preview, name="jira_bulk_labels_preview"),
    path("bulk/labels/apply/", views.bulk_labels_apply, name="jira_bulk_labels_apply"),

    path("configs/", views.jira_config_list, name="jira_config_list"),

    path("configs/modal/create/", views.jira_config_modal_create, name="jira_config_modal_create"),
    path("configs/modal/<int:pk>/edit/", views.jira_config_modal_edit, name="jira_config_modal_edit"),
    path("configs/<int:pk>/delete/", views.jira_config_delete, name="jira_config_delete"),
    path("configs/<int:pk>/test/", views.jira_config_test, name="jira_config_test"),
    path("configs/set-current/", views.jira_config_set_current, name="jira_config_set_current"),
]
