(function () {
    const page = window.SPRINT_LIST_PAGE;
    if (!page || !window.jQuery) return;

    const dataTableLanguageUrl = page.datatableLanguageUrl
        || window.AppUI?.getDataTableLanguageUrl(page.languageCode, "1.13.6");

    $(document).ready(function () {
        $("#sprintsTable").DataTable({
            pageLength: 25,
            order: [[0, "asc"]],
            language: {url: dataTableLanguageUrl}
        });
    });
})();
