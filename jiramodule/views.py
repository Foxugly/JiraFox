from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from jiramodule.services.bulk_operations import (
    BULK_LABELS_SESSION_KEY,
    apply_bulk_label_preview,
    build_bulk_label_preview,
    clamp_limit,
)
from jiramodule.services.jira_config_management import (
    build_jira_config_form,
    get_user_jira_config_or_404,
    save_jira_config_form,
    set_current_config,
    test_jira_configuration,
)
from jiramodule.services.jira_config_service import get_connected_jira_for_user


def render_jira_config_form_payload(*, request, form, mode: str, config=None) -> dict:
    context = {"form": form, "mode": mode}
    if config is not None:
        context["cfg"] = config
    html = render_to_string("jira/partials/jira_config_modal_form.html", context, request=request)
    return {"ok": False, "html": html}


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
def bulk_operations_home(request):
    return render(request, "jira/bulk_operations.html")


@login_required
def bulk_labels_page(request):
    return render(
        request,
        "jira/bulk_labels.html",
        {
            "limit": clamp_limit(request.POST.get("limit") or request.GET.get("limit")),
            "preview": request.session.get(BULK_LABELS_SESSION_KEY),
            "results": request.session.pop("bulk_labels_results", None),
        },
    )


@login_required
@require_POST
def bulk_labels_preview(request):
    jql = (request.POST.get("jql") or "").strip()
    add_labels = request.POST.get("add_labels") or ""
    remove_labels = request.POST.get("remove_labels") or ""
    limit = request.POST.get("limit")

    if not jql:
        messages.error(request, _("JQL is required."))
        return redirect("jira_bulk_labels")
    if not add_labels.strip() and not remove_labels.strip():
        messages.error(request, _("Add at least one label to add or remove."))
        return redirect("jira_bulk_labels")

    jira = get_connected_jira_for_user(request.user)
    try:
        preview = build_bulk_label_preview(
            jira,
            jql=jql,
            add_labels_raw=add_labels,
            remove_labels_raw=remove_labels,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        messages.error(request, _("Preview failed: %(error)s") % {"error": exc})
        return redirect("jira_bulk_labels")

    request.session[BULK_LABELS_SESSION_KEY] = preview.as_session_payload()
    messages.success(request, _("Preview ready: %(count)s ticket(s) will change.") % {"count": preview.changed_count})
    return redirect("jira_bulk_labels")


@login_required
@require_POST
def bulk_labels_apply(request):
    payload = request.session.get(BULK_LABELS_SESSION_KEY)
    if not payload:
        messages.error(request, _("No bulk labels preview to apply."))
        return redirect("jira_bulk_labels")

    jira = get_connected_jira_for_user(request.user)
    results = apply_bulk_label_preview(jira, payload)
    request.session["bulk_labels_results"] = results
    request.session.pop(BULK_LABELS_SESSION_KEY, None)

    success_count = sum(1 for result in results if result.get("ok") and not result.get("skipped"))
    error_count = sum(1 for result in results if not result.get("ok"))
    messages.success(
        request,
        _("Bulk labels applied: %(success_count)s updated, %(error_count)s error(s).")
        % {"success_count": success_count, "error_count": error_count},
    )
    return redirect("jira_bulk_labels")


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
    try:
        config_id = int(request.POST["config_id"])
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid config_id."}, status=400)
    want_current = request.POST.get("current") == "true"
    config = get_user_jira_config_or_404(request.user, config_id)
    set_current_config(config=config, want_current=want_current)
    return JsonResponse({"ok": True})
