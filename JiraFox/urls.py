"""
URL configuration for JiraFox project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView

from JiraFox.views import dashboard_summary_api, dashboard_items_api, HomeView

urlpatterns = [
    path("api/dashboard/summary/", dashboard_summary_api, name="dashboard-summary-api"),
    path("api/dashboard/items/", dashboard_items_api, name="dashboard-items-api"),
    path("", HomeView.as_view(), name="home"),
    path("settings", TemplateView.as_view(template_name="base.html"), name="settings"),
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('jira/', include('jiramodule.urls')),
    path('sprint/', include('sprint.urls')),
    path('issue/', include('issue.urls')),
    path("i18n/", include("django.conf.urls.i18n")),
]
