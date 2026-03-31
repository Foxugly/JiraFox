(function () {
    const page = window.SPRINT_WIA_PAGE;
    if (!page) return;

    const badge = document.getElementById("statusBadge");
    const reloadButton = document.getElementById("reloadBtn");
    const tooltipEl = document.getElementById("chartTooltip");
    const ttKey = document.getElementById("ttKey");
    const ttTitle = document.getElementById("ttTitle");
    const ttStatus = document.getElementById("ttStatus");
    const ttAge = document.getElementById("ttAge");
    const ttLink = document.getElementById("ttLink");
    const countState = {statuses: [], counts: {}};

    function notify(message, kind, delay) {
        window.AppUI?.showToast(message, kind, delay);
    }

    function setBadge(text, kind) {
        if (!badge) return;
        badge.className = `badge text-bg-${kind || "secondary"}`;
        badge.textContent = text;
    }

    function ageBadgeClass(ageDays) {
        if (ageDays < 10) return "text-bg-success";
        if (ageDays < 20) return "text-bg-warning";
        return "text-bg-danger";
    }

    async function loadData() {
        setBadge("Loading...", "secondary");
        try {
            const response = await fetch(page.apiUrl, {headers: {"Accept": "application/json"}});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (!data || !Array.isArray(data.items)) throw new Error("Invalid payload: missing items");
            if (!Array.isArray(data.statuses) || data.statuses.length === 0) {
                data.statuses = [...new Set(data.items.map((item) => item.status))];
            }
            setBadge("OK (API)", "success");
            return data;
        } catch (error) {
            console.warn("WIA API load failed", error);
            setBadge("API error", "danger");
            notify(`Unable to load WIA data: ${error.message}`, "danger", 4500);
            throw error;
        }
    }

    function hideTooltip() {
        if (tooltipEl) tooltipEl.style.display = "none";
    }

    function externalTooltipHandler(context) {
        const {chart, tooltip} = context;
        if (!tooltipEl) return;
        if (tooltip.opacity === 0) {
            hideTooltip();
            return;
        }

        const point = tooltip.dataPoints?.[0];
        if (!point || point.dataset.type !== "scatter") {
            hideTooltip();
            return;
        }

        const raw = point.raw || {};
        const age = raw.y ?? raw.ageDays ?? "?";
        ttKey.textContent = raw.key ?? "-";
        ttTitle.textContent = raw.title ?? "";
        ttStatus.textContent = raw.status ?? "-";
        ttAge.textContent = `${age} d`;
        ttAge.className = `badge badge-age ${ageBadgeClass(Number(age) || 999)}`;
        ttLink.href = raw.url || "#";
        ttLink.style.pointerEvents = raw.url ? "auto" : "none";

        const rect = chart.canvas.getBoundingClientRect();
        tooltipEl.style.left = `${window.scrollX + rect.left + tooltip.caretX}px`;
        tooltipEl.style.top = `${window.scrollY + rect.top + tooltip.caretY}px`;
        tooltipEl.style.display = "block";
    }

    const countPlugin = {
        id: "countPlugin",
        afterDatasetsDraw(chart) {
            const statuses = countState.statuses || [];
            const counts = countState.counts || {};
            if (!statuses.length) return;

            const {ctx, scales: {x}, chartArea} = chart;
            ctx.save();
            ctx.font = "12px system-ui";
            ctx.fillStyle = "#495057";
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            const yTopInside = chartArea.top + 6;

            statuses.forEach((status, index) => {
                const xPos = x.getPixelForValue(index);
                const count = counts[status] ?? 0;
                ctx.fillText(`${count} item${count > 1 ? "s" : ""}`, xPos, yTopInside);
            });
            ctx.restore();
        }
    };
    Chart.register(countPlugin);

    function buildChartPayload(apiData) {
        const items = apiData.items || [];
        const statuses = Array.isArray(apiData.statuses) && apiData.statuses.length
            ? apiData.statuses
            : [...new Set(items.map((item) => item.status))];
        const statusIndex = Object.fromEntries(statuses.map((status, index) => [status, index]));
        const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
        items.forEach((item) => {
            if (item.status in counts) counts[item.status] += 1;
        });

        const maxItemAge = items.length ? Math.max(...items.map((item) => Number(item.ageDays ?? 0))) : 0;
        const maxY = maxItemAge + 10;
        const makeBarPoints = (height) => statuses.map((_, index) => ({x: index, y: height}));
        const scatterData = items
            .filter((item) => statusIndex[item.status] !== undefined)
            .map((item) => ({
                x: statusIndex[item.status],
                y: Number(item.ageDays ?? 0),
                key: item.key,
                status: item.status,
                title: item.title ?? "",
                url: item.url ?? ""
            }));

        return {
            statuses,
            counts,
            maxY,
            datasets: [
                {type: "bar", label: "0-10", data: makeBarPoints(10), backgroundColor: "rgba(46,204,113,0.35)", borderWidth: 0, stack: "zones", order: 2, barThickness: 45},
                {type: "bar", label: "10-20", data: makeBarPoints(10), backgroundColor: "rgba(243,156,18,0.35)", borderWidth: 0, stack: "zones", order: 2, barThickness: 45},
                {type: "bar", label: "20-50", data: makeBarPoints(30), backgroundColor: "rgba(231,76,60,0.35)", borderWidth: 0, stack: "zones", order: 2, barThickness: 45},
                {type: "scatter", label: "Items", data: scatterData, pointRadius: 5, pointHoverRadius: 7, backgroundColor: "#111", borderColor: "#111", order: 1}
            ]
        };
    }

    let dataTable = null;

    function renderTable(items) {
        const sorted = [...items].sort((a, b) => Number(b.ageDays || 0) - Number(a.ageDays || 0));
        if (dataTable) {
            dataTable.clear();
            dataTable.rows.add(sorted);
            dataTable.draw();
            return;
        }

        dataTable = $("#wiaTable").DataTable({
            data: sorted,
            responsive: true,
            pageLength: 25,
            lengthMenu: [10, 25, 50, 100],
            order: [[3, "desc"]],
            columns: [
                {
                    data: "key",
                    render: (data, type) => {
                        if (type !== "display") return data;
                        const href = page.issueDetailUrlTemplate.replace("__KEY__", encodeURIComponent(data));
                        return `<a href="${href}" class="fw-semibold text-decoration-none">${data}</a>`;
                    }
                },
                {data: "title"},
                {data: "status"},
                {data: "ageDays", render: (data) => `<span class="badge ${ageBadgeClass(Number(data) || 0)}">${Number(data) || 0}</span>`},
                {data: "cycleTime", render: (data) => `<span class="badge ${ageBadgeClass(Number(data) || 0)}">${Number(data) || 0}</span>`},
                {
                    data: "first_in_progress",
                    render: (data, type, row) => {
                        if (!data) return "";
                        if (type === "display" || type === "filter") return row.first_in_progress_display || "";
                        return data;
                    }
                },
                {
                    data: "url",
                    orderable: false,
                    searchable: false,
                    className: "text-end",
                    render: (data) => `<a class="btn btn-sm btn-outline-primary" href="${data || "#"}" target="_blank" rel="noopener noreferrer"><i class="fa-brands fa-jira"></i></a>`
                }
            ]
        });
    }

    let chart = null;

    async function renderAll() {
        hideTooltip();
        reloadButton?.setAttribute("disabled", "disabled");

        try {
            const apiData = await loadData();
            renderTable(apiData.items || []);
            const payload = buildChartPayload(apiData);
            countState.statuses = payload.statuses;
            countState.counts = payload.counts;
            const canvas = document.getElementById("ageChart");
            if (!canvas) return;

            if (chart) chart.destroy();
            chart = new Chart(canvas, {
                data: {datasets: payload.datasets},
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    layout: {padding: {top: 24}},
                    interaction: {mode: "nearest", intersect: true},
                    plugins: {
                        legend: {position: "bottom"},
                        tooltip: {enabled: false, external: externalTooltipHandler}
                    },
                    onClick: (event) => {
                        const points = chart.getElementsAtEventForMode(event, "nearest", {intersect: true}, true);
                        if (!points.length) return;
                        const point = points[0];
                        const dataset = chart.data.datasets[point.datasetIndex];
                        if (dataset.type !== "scatter") return;
                        const raw = dataset.data[point.index];
                        if (raw?.url) window.open(raw.url, "_blank", "noopener,noreferrer");
                    },
                    scales: {
                        x: {
                            type: "linear",
                            min: -0.6,
                            max: payload.statuses.length - 0.4,
                            ticks: {stepSize: 1, callback: (value) => payload.statuses[value] ?? ""},
                            grid: {display: false}
                        },
                        y: {
                            stacked: true,
                            beginAtZero: true,
                            max: payload.maxY,
                            title: {display: true, text: "Item age (days)"}
                        }
                    }
                }
            });
        } finally {
            reloadButton?.removeAttribute("disabled");
        }
    }

    reloadButton?.addEventListener("click", renderAll);
    renderAll();
})();
