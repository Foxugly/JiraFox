from django.db import transaction
from django.shortcuts import render

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from .forms import JiraConfigurationForm
from .models import JiraConfiguration

import logging

from .services.jira_client import JiraManager

logger = logging.getLogger(__name__)

def _user_has_config(user, config: JiraConfiguration) -> bool:
    return user.jira_configs.filter(pk=config.pk).exists()

@login_required
@require_POST
def jira_config_test(request, pk: int):
    cfg = get_object_or_404(JiraConfiguration, pk=pk)
    if not _user_has_config(request.user, cfg):
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)

    try:
        jm = JiraManager(cfg)
        jm.connect()
        _ = jm.server_info()
        ok = True
        error = ""
    except Exception as e:
        ok = False
        error = str(e)
        logger.exception("jira_config_test failed for cfg=%s user=%s", cfg.pk, request.user.pk)

    # ✅ si tu as ajouté les champs last_test_*
    if hasattr(cfg, "mark_test_result"):
        cfg.mark_test_result(ok=ok, error=error)
    else:
        cfg.is_active = ok
        cfg.save(update_fields=["is_active"])

    return JsonResponse({"ok": True, "result": ok, "error": error})

@login_required
def jira_config_list(request):
    configs = request.user.jira_configs.all().order_by("-id")
    return render(request, "jira/jira_config_list.html", {"configs": configs})


@login_required
def jira_config_modal_create(request):
    if request.method == "POST":
        form = JiraConfigurationForm(request.POST)
        if form.is_valid():
            cfg = form.save(commit=False)
            cfg.user = request.user
            cfg.is_active = False
            cfg.save(user=request.user)
            return JsonResponse({"ok": True})

        return JsonResponse({
            "ok": False,
            "html": render(
                request,
                "jira/partials/jira_config_modal_form.html",
                {"form": form, "mode": "create"},
            ).content.decode("utf-8")
        })

    form = JiraConfigurationForm()
    return render(request, "jira/partials/jira_config_modal_form.html", {"form": form, "mode": "create"})


@login_required
def jira_config_modal_edit(request, pk: int):
    cfg = get_object_or_404(JiraConfiguration, pk=pk)
    if not _user_has_config(request.user, cfg):
        return HttpResponseForbidden("Not allowed")

    if request.method == "POST":
        form = JiraConfigurationForm(request.POST, instance=cfg)
        if form.is_valid():
            cfg = form.save(commit=False)
            cfg.is_active = False  # à re-tester après modif
            cfg.save(user=request.user)
            return JsonResponse({"ok": True})
        return JsonResponse({"ok": False, "html": render(request, "jira/partials/jira_config_modal_form.html", {"form": form, "mode": "edit", "cfg": cfg}).content.decode("utf-8")})

    form = JiraConfigurationForm(instance=cfg)
    return render(request, "jira/partials/jira_config_modal_form.html", {"form": form, "mode": "edit", "cfg": cfg})

@login_required
@require_POST
def jira_config_delete(request, pk: int):
    cfg = get_object_or_404(JiraConfiguration, pk=pk)
    if cfg.user_id != request.user.id:
        return JsonResponse({"ok": False, "error": "Not allowed"}, status=403)

    cfg.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def jira_config_set_current(request):
    config_id = int(request.POST.get("config_id"))
    want_current = request.POST.get("current") == "true"

    cfg = get_object_or_404(JiraConfiguration, pk=config_id, user=request.user)

    with transaction.atomic():
        if want_current:
            cfg.set_as_current()
        else:
            # autoriser "aucune current" (si tu veux l'interdire, refuse ici)
            cfg.current = False
            cfg.save(update_fields=["current"])

    return JsonResponse({"ok": True})