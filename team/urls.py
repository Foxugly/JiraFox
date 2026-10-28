
from django.urls import path
from . import views

urlpatterns = [
    path("dev/update/<int:dev_id>/", views.update_dev_field, name="update_dev"),
    path("team/<int:team_id>/dev/create/", views.create_dev_in_team, name="create_dev"),
    path("teamdev/<int:teamdev_id>/delete/", views.delete_dev_from_team, name="delete_dev_from_team"),
    path("team/<int:team_id>/reorder/", views.reorder_teamdevs, name="reorder_teamdevs"),

    path("projects/", views.project_list, name="project_list"),
    path("projects/new/", views.project_create, name="project_create"),
    path("projects/current/", views.project_set_current, name="project_set_current"),
    path("projects/<int:project_id>/", views.project_edit, name="project_edit"),
    path("projects/<int:project_id>/delete/", views.project_delete, name="project_delete"),
    path("projects/<int:project_id>/devs/", views.project_devs, name="project_devs"),
    path("projects/<int:project_id>/members/add/", views.project_add_member, name="project_add_member"),
    path(
        "projects/<int:project_id>/members/<int:user_id>/remove/",
        views.project_remove_member,
        name="project_remove_member",
    ),
]
