from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from jiramodule.services.jira_config_management import (
    build_jira_config_form,
    get_user_jira_config_or_404,
    render_jira_config_form_payload,
    save_jira_config_form,
    set_current_config,
    test_jira_configuration,
)


@login_required
@require_POST
def jira_config_test(request, pk: int):
    config = get_user_jira_config_or_404(request.user, pk)
    ok, error = test_jira_configuration(config, user_pk=request.user.pk)
    return JsonResponse({"ok": True, "result": ok, "error": error})


@login_required
def jira_config_list(request):
    configs = request.user.jira_configs.all().order_by("-id")
    return render(request, "jira/jira_config_list.html", {"configs": configs})


@login_required
def jira_config_modal_create(request):
    if request.method == "POST":
        form = build_jira_config_form(data=request.POST)
        if form.is_valid():
            save_jira_config_form(form=form, user=request.user)
            return JsonResponse({"ok": True})
        return JsonResponse(render_jira_config_form_payload(request=request, form=form, mode="create"))

    form = build_jira_config_form()
    return render(
        request,
        "jira/partials/jira_config_modal_form.html",
        {"form": form, "mode": "create"},
    )


@login_required
def jira_config_modal_edit(request, pk: int):
    config = get_user_jira_config_or_404(request.user, pk)

    if request.method == "POST":
        form = build_jira_config_form(data=request.POST, instance=config)
        if form.is_valid():
            save_jira_config_form(form=form, user=request.user)
            return JsonResponse({"ok": True})
        return JsonResponse(
            render_jira_config_form_payload(
                request=request,
                form=form,
                mode="edit",
                config=config,
            )
        )

    form = build_jira_config_form(instance=config)
    return render(
        request,
        "jira/partials/jira_config_modal_form.html",
        {"form": form, "mode": "edit", "cfg": config},
    )


@login_required
@require_POST
def jira_config_delete(request, pk: int):
    config = get_user_jira_config_or_404(request.user, pk)
    config.delete()
    return JsonResponse({"ok": True})


@login_required
@require_POST
def jira_config_set_current(request):
    config_id = int(request.POST.get("config_id"))
    want_current = request.POST.get("current") == "true"
    config = get_user_jira_config_or_404(request.user, config_id)
    set_current_config(config=config, want_current=want_current)
    return JsonResponse({"ok": True})
