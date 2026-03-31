(function () {
    const page = window.SPRINT_XLS_PAGE || {};
    const i18n = page.i18n || {};
    const addButton = document.getElementById("add-dev-btn");
    const headerRow = document.getElementById("dev-header");
    const table = document.getElementById("dev-table");
    if (!table || !headerRow) return;

    function notify(message, kind, delay) {
        window.AppUI?.showToast(message, kind, delay);
    }

    function getCsrfToken() {
        const input = document.querySelector('#csrf-form input[name="csrfmiddlewaretoken"]');
        return input ? input.value : "";
    }

    const csrfToken = getCsrfToken();

    document.querySelectorAll("#dev-table .dev-input").forEach((input) => {
        const saveInput = async () => {
            try {
                const response = await fetch(input.dataset.updateUrl, {
                    method: "POST",
                    headers: {"X-CSRFToken": csrfToken},
                    body: new URLSearchParams({
                        field: input.dataset.field,
                        value: input.value,
                    }),
                });
                if (!response.ok) throw new Error(await response.text());

                input.classList.remove("is-invalid");
                input.classList.add("is-valid");
                notify(i18n.value_saved || "Value saved.", "success", 1200);
                window.setTimeout(() => input.classList.remove("is-valid"), 600);
            } catch (error) {
                input.classList.add("is-invalid");
                notify(i18n.unable_to_save_value || "Unable to save value.", "danger", 3500);
                console.error(error);
            }
        };

        input.addEventListener("change", saveInput);
        input.addEventListener("blur", saveInput);
    });

    addButton?.addEventListener("click", async () => {
        const name = await window.AppUI?.promptAction({
            title: i18n.add_developer || "Add developer",
            label: i18n.developer_name || "Developer name",
            placeholder: "Jane Doe",
            confirmLabel: i18n.create || "Create",
            cancelLabel: i18n.cancel || "Cancel",
            required: true,
        });
        if (!name) return;

        try {
            const response = await fetch(addButton.dataset.createUrl, {
                method: "POST",
                headers: {"X-CSRFToken": csrfToken},
                body: new URLSearchParams({name}),
            });
            if (!response.ok) throw new Error(await response.text());

            notify(i18n.developer_added || "Developer added.", "success");
            window.location.reload();
        } catch (error) {
            notify(i18n.unable_to_create_developer || "Unable to create developer.", "danger", 4000);
            console.error(error);
        }
    });

    document.querySelectorAll("#dev-header .delete-dev").forEach((button) => {
        button.addEventListener("click", async (event) => {
            event.stopPropagation();
            const confirmed = await window.AppUI?.confirmAction(i18n.remove_developer_question || "Remove this developer from the team?", {
                title: i18n.remove_developer || "Remove developer",
                confirmLabel: i18n.remove || "Remove",
                confirmClass: "btn-danger",
                cancelLabel: i18n.cancel || "Cancel",
            });
            if (!confirmed) return;

            try {
                const response = await fetch(button.dataset.deleteUrl, {
                    method: "POST",
                    headers: {"X-CSRFToken": csrfToken},
                });
                if (!response.ok) throw new Error(await response.text());

                notify(i18n.developer_removed || "Developer removed from team.", "success");
                window.location.reload();
            } catch (error) {
                notify(i18n.unable_to_remove_developer || "Unable to remove developer.", "danger", 4000);
                console.error(error);
            }
        });
    });

    let draggingHeader = null;
    let draggingIndex = -1;

    function getHeaderIndex(headerCell) {
        return Array.from(headerRow.children).indexOf(headerCell);
    }

    function moveColumn(fromIndex, toIndex) {
        if (fromIndex === toIndex) return;
        document.querySelectorAll("#dev-table tr").forEach((row) => {
            const cells = Array.from(row.children);
            const fromCell = cells[fromIndex];
            const toCell = cells[toIndex];
            if (fromIndex < toIndex) {
                row.insertBefore(fromCell, toCell.nextSibling);
            } else {
                row.insertBefore(fromCell, toCell);
            }
        });
    }

    headerRow.addEventListener("dragstart", (event) => {
        const headerCell = event.target.closest("th.dev-col");
        if (!headerCell) return;
        draggingHeader = headerCell;
        draggingIndex = getHeaderIndex(headerCell);
        headerCell.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", headerCell.dataset.teamdevId || "");
    });

    headerRow.addEventListener("dragover", (event) => {
        event.preventDefault();
        const overHeader = event.target.closest("th.dev-col");
        if (!overHeader || overHeader === draggingHeader) return;
        const overIndex = getHeaderIndex(overHeader);
        if (draggingIndex < 1 || overIndex < 1) return;
        moveColumn(draggingIndex, overIndex);
        draggingIndex = overIndex;
    });

    headerRow.addEventListener("drop", async (event) => {
        event.preventDefault();
        if (!draggingHeader) return;

        draggingHeader.classList.remove("dragging");
        const ids = Array.from(headerRow.querySelectorAll("th.dev-col")).map((headerCell) => headerCell.dataset.teamdevId);

        try {
            const body = new URLSearchParams();
            ids.forEach((id) => body.append("ids[]", id));
            const response = await fetch(table.dataset.reorderUrl, {
                method: "POST",
                headers: {"X-CSRFToken": csrfToken},
                body,
            });
            if (!response.ok) throw new Error(await response.text());
            notify(i18n.column_order_saved || "Column order saved.", "success", 1600);
        } catch (error) {
            console.error(error);
            notify(i18n.unable_to_save_column_order || "Unable to save column order.", "danger", 4000);
        } finally {
            draggingHeader = null;
            draggingIndex = -1;
        }
    });

    headerRow.addEventListener("dragend", () => {
        if (draggingHeader) draggingHeader.classList.remove("dragging");
        draggingHeader = null;
        draggingIndex = -1;
    });
})();
