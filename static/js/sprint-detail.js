(function () {
    const page = window.SPRINT_DETAIL_PAGE;
    if (!page || !window.jQuery) return;

    const dataTableLanguageUrl = page.datatableLanguageUrl
        || window.AppUI?.getDataTableLanguageUrl(page.languageCode, "1.13.6");

    $(document).ready(function () {
        const dt = $("#issuesTable").DataTable({
            pageLength: 25,
            order: [[0, "asc"]],
            language: {url: dataTableLanguageUrl},
            ajax: {
                url: page.apiUrl,
                dataSrc: function (json) {
                    return json.items || [];
                }
            },
            columns: [
                {
                    data: "key",
                    render: function (data, type) {
                        if (type !== "display") return data;
                        const href = page.issueDetailUrlTemplate.replace("__KEY__", encodeURIComponent(data));
                        return `<a class="fw-semibold text-decoration-none mono" href="${href}">${data}</a>`;
                    }
                },
                {data: "summary"},
                {data: "issueType"},
                {data: "state"},
                {data: "assignee"},
                {
                    data: null,
                    orderable: false,
                    searchable: false,
                    className: "text-end",
                    render: function (row) {
                        const key = row.key || "";
                        const jiraUrl = row.url || "#";
                        const detailUrl = page.issueDetailUrlTemplate.replace("__KEY__", encodeURIComponent(key));
                        return `<div class="btn-group btn-group-sm" role="group">
                            <a class="btn btn-sm btn-outline-primary" href="${jiraUrl}" target="_blank" rel="noopener noreferrer">
                                <i class="fa-brands fa-jira"></i>
                            </a>
                            <a class="btn btn-outline-secondary" href="${detailUrl}">
                                <i class="fa-solid fa-up-right-from-square"></i>
                            </a>
                        </div>`;
                    }
                }
            ]
        });

        $("#reloadBtn").on("click", function () {
            dt.ajax.reload(null, false);
        });
    });
})();
