(function () {
    const page = window.JIRA_CONFIGS_PAGE;
    if (!page) return;

    const modalContent = document.getElementById("jiraConfigModalContent");
    const addButton = document.getElementById("btnAddConfig");

    function notify(message, kind, delay) {
        window.AppUI?.showToast(message, kind, delay);
    }

    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(";").shift();
        return "";
    }

    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        const contentType = (response.headers.get("content-type") || "").toLowerCase();
        if (!contentType.includes("application/json")) {
            const text = await response.text();
            console.error("Non JSON response", response.status, text.slice(0, 800));
            throw new Error(`Unexpected response format (${response.status})`);
        }
        return response.json();
    }

    async function loadModal(url) {
        const response = await fetch(url, {
            headers: {"X-Requested-With": "XMLHttpRequest"},
            credentials: "same-origin",
        });
        modalContent.innerHTML = await response.text();
        initModal(modalContent);
    }

    function initModal(root) {
        const form = root.querySelector("#jiraConfigForm");
        if (!form) return;

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submitButton = root.querySelector("#jiraConfigSubmitBtn");
            const oldLabel = submitButton ? submitButton.textContent : "";

            if (submitButton) {
                submitButton.disabled = true;
                submitButton.textContent = "Saving...";
            }

            try {
                const data = await fetchJson(form.action, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": getCookie("csrftoken"),
                    },
                    credentials: "same-origin",
                    body: new FormData(form),
                });

                if (data.ok) {
                    notify("Configuration saved.", "success");
                    window.location.reload();
                    return;
                }

                if (data.html) {
                    modalContent.innerHTML = data.html;
                    initModal(modalContent);
                }
            } catch (error) {
                notify(error.message, "danger", 4500);
            } finally {
                if (submitButton) {
                    submitButton.disabled = false;
                    submitButton.textContent = oldLabel;
                }
            }
        }, {once: true});

        const testButton = root.querySelector("#btnTestJira");
        const resultBox = root.querySelector("#jiraTestResult");
        if (!testButton || !resultBox) return;

        testButton.addEventListener("click", async () => {
            const oldLabel = testButton.textContent;
            testButton.disabled = true;
            testButton.textContent = "Testing...";
            resultBox.className = "alert alert-info";
            resultBox.textContent = "Connection test in progress...";
            resultBox.classList.remove("d-none");

            try {
                const data = await fetchJson(testButton.dataset.url, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": getCookie("csrftoken"),
                    },
                    credentials: "same-origin",
                });

                if (data.ok && data.result === true) {
                    resultBox.className = "alert alert-success";
                    resultBox.textContent = "Connection OK";
                    notify("Jira connection validated.", "success");
                    return;
                }

                resultBox.className = "alert alert-danger";
                resultBox.textContent = `Connection failed: ${data.error || "unknown error"}`;
                notify("Jira connection failed.", "danger", 4500);
            } catch (error) {
                resultBox.className = "alert alert-danger";
                resultBox.textContent = error.message;
                notify(error.message, "danger", 4500);
            } finally {
                testButton.disabled = false;
                testButton.textContent = oldLabel;
            }
        }, {once: true});
    }

    addButton?.addEventListener("click", (event) => {
        loadModal(event.currentTarget.dataset.url);
    });

    document.querySelectorAll(".btn-edit").forEach((button) => {
        button.addEventListener("click", (event) => loadModal(event.currentTarget.dataset.url));
    });

    document.querySelectorAll(".btn-delete").forEach((button) => {
        button.addEventListener("click", async (event) => {
            const confirmed = await window.AppUI?.confirmAction("Delete this Jira configuration?", {
                title: "Delete configuration",
                confirmLabel: "Delete",
                confirmClass: "btn-danger",
                cancelLabel: "Cancel",
            });
            if (!confirmed) return;

            const url = event.currentTarget.dataset.url;
            try {
                const data = await fetchJson(url, {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": getCookie("csrftoken"),
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    credentials: "same-origin",
                });
                if (data.ok) {
                    event.currentTarget.closest("tr")?.remove();
                    notify("Configuration deleted.", "success");
                }
            } catch (error) {
                notify(error.message, "danger", 4500);
            }
        });
    });

    document.querySelectorAll(".btn-test").forEach((button) => {
        button.addEventListener("click", async (event) => {
            const currentButton = event.currentTarget;
            const oldHtml = currentButton.innerHTML;
            currentButton.disabled = true;
            currentButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';

            try {
                const data = await fetchJson(currentButton.dataset.url, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": getCookie("csrftoken"),
                    },
                    credentials: "same-origin",
                });
                if (data.ok && data.result === true) {
                    notify("Jira connection OK.", "success");
                } else {
                    notify(`Jira connection failed: ${data.error || "unknown error"}`, "danger", 5000);
                }
                window.location.reload();
            } catch (error) {
                notify(error.message, "danger", 4500);
            } finally {
                currentButton.disabled = false;
                currentButton.innerHTML = oldHtml;
            }
        });
    });

    document.querySelectorAll(".js-current-switch").forEach((toggle) => {
        toggle.addEventListener("change", async (event) => {
            const input = event.currentTarget;
            const configId = input.dataset.configId;
            const wantCurrent = input.checked;

            if (wantCurrent) {
                document.querySelectorAll(".js-current-switch").forEach((other) => {
                    if (other !== input) other.checked = false;
                });
            }

            const body = new FormData();
            body.append("config_id", configId);
            body.append("current", String(wantCurrent));

            try {
                const data = await fetchJson(page.setCurrentUrl, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": getCookie("csrftoken"),
                    },
                    credentials: "same-origin",
                    body,
                });

                if (!data.ok) {
                    notify(data.error || "Unable to save current configuration.", "danger", 4500);
                    window.location.reload();
                    return;
                }

                notify("Current configuration updated.", "success");
            } catch (error) {
                notify(error.message, "danger", 4500);
                window.location.reload();
            }
        });
    });
})();
