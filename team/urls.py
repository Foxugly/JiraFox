
from django.urls import path
from . import views

urlpatterns = [
    path("dev/update/<int:dev_id>/", views.update_dev_field, name="update_dev"),
    path("team/<int:team_id>/dev/create/", views.create_dev_in_team, name="create_dev"),
    path("teamdev/<int:teamdev_id>/delete/", views.delete_dev_from_team, name="delete_dev_from_team"),
    path("team/<int:team_id>/reorder/", views.reorder_teamdevs, name="reorder_teamdevs"),
]
