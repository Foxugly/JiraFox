(function () {
    const toastContainer = document.getElementById("appToastContainer");
    const confirmModalEl = document.getElementById("appConfirmModal");
    const promptModalEl = document.getElementById("appPromptModal");
    const confirmModal = confirmModalEl ? new bootstrap.Modal(confirmModalEl) : null;
    const promptModal = promptModalEl ? new bootstrap.Modal(promptModalEl) : null;

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function showToast(message, kind, delay) {
        if (!toastContainer) return;

        const toast = document.createElement("div");
        toast.className = "toast align-items-center border-0";
        toast.setAttribute("role", "alert");
        toast.setAttribute("aria-live", "assertive");
        toast.setAttribute("aria-atomic", "true");
        toast.innerHTML = [
            '<div class="d-flex">',
            `<div class="toast-body text-bg-${escapeHtml(kind || "primary")} rounded-start">${escapeHtml(message)}</div>`,
            '<button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>',
            "</div>",
        ].join("");

        toastContainer.appendChild(toast);
        const instance = new bootstrap.Toast(toast, {delay: delay || 3000});
        toast.addEventListener("hidden.bs.toast", () => toast.remove(), {once: true});
        instance.show();
    }

    function withModalLifecycle(modalEl, onHidden) {
        if (!modalEl) return;
        modalEl.addEventListener("hidden.bs.modal", onHidden, {once: true});
    }

    function confirmAction(message, options) {
        if (!confirmModalEl || !confirmModal) {
            return Promise.resolve(window.confirm(message));
        }

        return new Promise((resolve) => {
            const titleEl = document.getElementById("appConfirmTitle");
            const messageEl = document.getElementById("appConfirmMessage");
            const cancelBtn = confirmModalEl.querySelector('[data-action="cancel"]');
            const confirmBtn = confirmModalEl.querySelector('[data-action="confirm"]');
            const title = options?.title || "Confirmation";
            const confirmLabel = options?.confirmLabel || "Confirm";
            const cancelLabel = options?.cancelLabel || "Cancel";
            let settled = false;

            titleEl.textContent = title;
            messageEl.textContent = message;
            confirmBtn.textContent = confirmLabel;
            cancelBtn.textContent = cancelLabel;
            confirmBtn.className = `btn ${options?.confirmClass || "btn-primary"}`;

            const close = (result) => {
                if (settled) return;
                settled = true;
                resolve(result);
                confirmModal.hide();
            };

            const onCancel = () => close(false);
            const onConfirm = () => close(true);
            const onHidden = () => {
                if (!settled) {
                    settled = true;
                    resolve(false);
                }
                cancelBtn.removeEventListener("click", onCancel);
                confirmBtn.removeEventListener("click", onConfirm);
            };

            cancelBtn.addEventListener("click", onCancel, {once: true});
            confirmBtn.addEventListener("click", onConfirm, {once: true});
            withModalLifecycle(confirmModalEl, onHidden);
            confirmModal.show();
        });
    }

    function promptAction(options) {
        if (!promptModalEl || !promptModal) {
            return Promise.resolve(window.prompt(options?.message || options?.label || "") || null);
        }

        return new Promise((resolve) => {
            const titleEl = document.getElementById("appPromptTitle");
            const labelEl = document.getElementById("appPromptLabel");
            const inputEl = document.getElementById("appPromptInput");
            const formEl = document.getElementById("appPromptForm");
            const cancelBtn = promptModalEl.querySelector('[data-action="cancel"]');
            const confirmBtn = promptModalEl.querySelector('[data-action="confirm"]');
            let settled = false;

            titleEl.textContent = options?.title || "Input";
            labelEl.textContent = options?.label || "Value";
            inputEl.value = options?.defaultValue || "";
            inputEl.placeholder = options?.placeholder || "";
            inputEl.required = Boolean(options?.required);
            confirmBtn.textContent = options?.confirmLabel || "Confirm";
            cancelBtn.textContent = options?.cancelLabel || "Cancel";
            confirmBtn.className = `btn ${options?.confirmClass || "btn-primary"}`;

            const close = (value) => {
                if (settled) return;
                settled = true;
                resolve(value);
                promptModal.hide();
            };

            const onCancel = () => close(null);
            const onSubmit = (event) => {
                event.preventDefault();
                const value = inputEl.value.trim();
                if (inputEl.required && !value) {
                    inputEl.classList.add("is-invalid");
                    return;
                }
                inputEl.classList.remove("is-invalid");
                close(value || null);
            };
            const onHidden = () => {
                if (!settled) {
                    settled = true;
                    resolve(null);
                }
                cancelBtn.removeEventListener("click", onCancel);
                formEl.removeEventListener("submit", onSubmit);
                inputEl.classList.remove("is-invalid");
            };

            cancelBtn.addEventListener("click", onCancel, {once: true});
            formEl.addEventListener("submit", onSubmit, {once: true});
            withModalLifecycle(promptModalEl, onHidden);
            promptModal.show();
            window.setTimeout(() => inputEl.focus(), 150);
        });
    }

    window.AppUI = {
        showToast,
        confirmAction,
        promptAction,
    };
})();
