(function () {
    const page = window.SPRINT_KANBAN_PAGE;
    if (!page) return;
    const i18n = page.i18n || {};

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function issueCard(issue) {
        const key = escapeHtml(issue.key);
        const summary = escapeHtml(issue.summary ?? "-");
        const assignee = escapeHtml(issue.assignee ?? "-");

        return `
            <button type="button"
                    class="card w-100 text-start mb-2 shadow-sm kanban-card"
                    data-issue-key="${key}"
                    style="cursor:pointer;">
                <div class="card-body py-3">
                    <div class="d-flex justify-content-between align-items-start gap-2">
                        <div class="text-truncate">
                            <div class="fw-semibold mono">${key}</div>
                            <div class="small text-muted text-truncate">${summary}</div>
                        </div>
                    </div>
                    <div class="mt-3 small text-muted"><i class="fa-solid fa-user me-2"></i>${assignee}</div>
                </div>
            </button>`;
    }

    function columnCard(column) {
        const title = escapeHtml(column.name);
        const issues = Array.isArray(column.issues) ? column.issues : [];
        const count = issues.length;

        return `
            <div class="kanban-column">
                <div class="utility-panel h-100">
                    <div class="d-flex align-items-center justify-content-between mb-3">
                        <div class="fw-semibold text-truncate">${title}</div>
                        <span class="badge text-bg-secondary">${count}</span>
                    </div>
                    <div style="max-height: 70vh; overflow:auto;">
                        ${count === 0 ? `<div class="text-muted small p-2">${escapeHtml(i18n.no_issue || "No issue")}</div>` : ""}
                        ${issues.map(issueCard).join("")}
                    </div>
                </div>
            </div>`;
    }

    async function loadKanban() {
        const root = document.getElementById("kanban");
        if (!root) return;

        root.innerHTML = `<div class="utility-panel"><div class="text-muted">${escapeHtml(i18n.loading || "Loading...")}</div></div>`;

        try {
            const response = await fetch(page.apiUrl, {headers: {"Accept": "application/json"}});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const columns = await response.json();
            root.innerHTML = Array.isArray(columns)
                ? columns.map(columnCard).join("")
                : `<div class="utility-panel"><div class="alert alert-warning mb-0">${escapeHtml(i18n.unexpected_response || "Unexpected response.")}</div></div>`;

            root.querySelectorAll(".kanban-card").forEach((button) => {
                button.addEventListener("click", () => {
                    const key = button.getAttribute("data-issue-key");
                    window.open(`https://jira.sm-ms.lan//browse/${encodeURIComponent(key)}`, "_blank", "noopener,noreferrer");
                });
            });
        } catch (error) {
            root.innerHTML = `<div class="utility-panel"><div class="alert alert-danger mb-0">${escapeHtml(i18n.unable_to_load_kanban || "Unable to load Kanban")}: ${escapeHtml(error.message)}</div></div>`;
        }
    }

    loadKanban();
})();
