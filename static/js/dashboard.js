(function () {
    const page = window.DASHBOARD_PAGE;
    if (!page) return;

    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach((element) => {
        new bootstrap.Tooltip(element);
    });

    const badgeSummary = document.getElementById("statusSummaryBadge");
    const badgeItems = document.getElementById("statusItemsBadge");
    const reloadButton = document.getElementById("reloadBtn");
    const tooltipEl = document.getElementById("chartTooltip");
    const ttKey = document.getElementById("ttKey");
    const ttTitle = document.getElementById("ttTitle");
    const ttStatus = document.getElementById("ttStatus");
    const ttAge = document.getElementById("ttAge");
    const ttLink = document.getElementById("ttLink");
    const i18n = page.i18n || {};

    function notify(message, kind, delay) {
        window.AppUI?.showToast(message, kind, delay);
    }

    function setBadge(element, text, kind) {
        if (!element) return;
        element.className = `badge text-bg-${kind || "secondary"}`;
        element.textContent = text;
    }

    function ageBadgeClass(days) {
        if (days < 10) return "text-bg-success";
        if (days < 20) return "text-bg-warning";
        return "text-bg-danger";
    }

    function safeNum(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) ? number : (fallback ?? 0);
    }

    async function loadJson(url) {
        const response = await fetch(url, {headers: {"Accept": "application/json"}});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
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
        const ageNumber = safeNum(age, 999);
        const xScale = chart.scales.x;
        const yScale = chart.scales.y;
        const rect = chart.canvas.getBoundingClientRect();

        ttKey.textContent = raw.key ?? "-";
        ttTitle.textContent = raw.title ?? "";
        ttStatus.textContent = raw.status ?? "-";
        ttAge.textContent = `${ageNumber} ${i18n.days || "days"}`;
        ttAge.className = `badge badge-age ${ageBadgeClass(ageNumber)}`;
        ttLink.href = raw.url || "#";
        ttLink.style.pointerEvents = raw.url ? "auto" : "none";
        tooltipEl.style.left = `${window.scrollX + rect.left + xScale.getPixelForValue(point.parsed.x)}px`;
        tooltipEl.style.top = `${window.scrollY + rect.top + yScale.getPixelForValue(point.parsed.y) - 200}px`;
        tooltipEl.style.display = "block";
    }

    async function loadSummary() {
        setBadge(badgeSummary, i18n.loading || "Loading...", "secondary");
        try {
            const data = await loadJson(page.apiSummaryUrl);
            setBadge(badgeSummary, i18n.ok_api || "OK (API)", "success");
            return data;
        } catch (error) {
            console.warn("summary fallback", error);
            setBadge(badgeSummary, i18n.fallback || "Fallback", "warning");
            notify("Summary API unavailable, using local fallback.", "warning", 3200);
            return page.fallbackSummary || {kpis: {}, wia: {items: []}, bottlenecks: [], links: {}};
        }
    }

    async function loadItems() {
        setBadge(badgeItems, i18n.loading || "Loading...", "secondary");
        try {
            const data = await loadJson(page.apiItemsUrl);
            setBadge(badgeItems, `Issues - ${i18n.ok_api || "OK (API)"}`, "success");
            return data;
        } catch (error) {
            console.warn("items fallback", error);
            setBadge(badgeItems, i18n.fallback || "Fallback", "warning");
            notify("Items API unavailable, using local fallback.", "warning", 3200);
            return page.fallbackItems || {items: []};
        }
    }

    function renderKpis(kpis) {
        const setText = (id, value) => {
            const element = document.getElementById(id);
            if (element) element.textContent = value;
        };

        setText("kpiWip", kpis?.wip ?? "-");
        setText("kpiWipSub", kpis?.wip_delta != null ? `${kpis.wip_delta >= 0 ? "+" : ""}${kpis.wip_delta} vs last week` : "-");
        setText("kpiAging", kpis?.aging_wip ?? "-");
        setText("kpiAgingSub", kpis?.aging_wip_pct != null ? `${kpis.aging_wip_pct}% ${i18n.items || "items"}` : "-");
        setText("kpiCycle", kpis?.cycle_p50_days != null ? `${kpis.cycle_p50_days}` : "-");
        setText("kpiLead", kpis?.lead_p50_days != null ? `${kpis.lead_p50_days}` : "-");
        setText("kpiThroughput", kpis?.throughput_7d ?? "-");
        setText("kpiFlowEff", kpis?.flow_eff_pct != null ? `${kpis.flow_eff_pct}%` : "-");
    }

    function renderBottlenecks(rows, links) {
        const body = document.querySelector("#bottlenecksTable tbody");
        if (!body) return;
        body.innerHTML = "";

        (rows || []).forEach((row) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td class="fw-semibold">${row.status ?? ""}</td>
                <td class="text-end">${row.count ?? ""}</td>
                <td class="text-end">${row.avgAge ?? ""}</td>
                <td class="text-end">${row.maxAge ?? ""}</td>
            `;
            body.appendChild(tr);
        });

        const link = document.getElementById("bottlenecksOpenJira");
        if (link) link.href = links?.open_jira_bottlenecks || "#";
    }

    const countPlugin = {
        id: "countPluginWia",
        afterDatasetsDraw(chart, args, options) {
            const statuses = options?.statuses || [];
            const counts = options?.counts || {};
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
                ctx.fillText(`${counts[status] ?? 0} ${i18n.items || "items"}`, xPos, yTopInside);
            });
            ctx.restore();
        }
    };
    Chart.register(countPlugin);

    let wiaChart = null;

    function buildWiaChart(wia, links) {
        const items = wia?.items || [];
        const statuses = Array.isArray(wia?.statuses) && wia.statuses.length
            ? wia.statuses
            : [...new Set(items.map((item) => item.status).filter(Boolean))];
        const statusIndex = Object.fromEntries(statuses.map((status, index) => [status, index]));
        const filteredItems = items.filter((item) => statusIndex[item.status] !== undefined);
        const counts = Object.fromEntries(statuses.map((status) => [status, 0]));
        items.forEach((item) => {
            if (item.status in counts) counts[item.status] += 1;
        });

        const maxAge = items.length ? Math.max(...items.map((item) => safeNum(item.ageDays, 0))) : 0;
        const maxY = maxAge + (wia?.maxYPad ?? 10);
        const makeBarPoints = (height) => statuses.map((_, index) => ({x: index, y: height}));
        const scatterData = filteredItems.map((item) => ({
            x: statusIndex[item.status],
            y: safeNum(item.ageDays, 0),
            key: item.key,
            title: item.title || "",
            status: item.status,
            url: item.url || "#",
            detail_url: item.detail_url || "#",
            assignee: item.assignee || "-"
        }));

        const canvas = document.getElementById("wiaChart");
        if (!canvas) return;

        if (wiaChart) wiaChart.destroy();
        hideTooltip();

        wiaChart = new Chart(canvas, {
            data: {
                datasets: [
                    {type: "bar", label: "0-10", data: makeBarPoints(10), backgroundColor: "rgba(46,204,113,0.35)", stack: "zones", order: 2, barThickness: 36, borderWidth: 0},
                    {type: "bar", label: "10-20", data: makeBarPoints(10), backgroundColor: "rgba(243,156,18,0.35)", stack: "zones", order: 2, barThickness: 36, borderWidth: 0},
                    {type: "bar", label: "20-50", data: makeBarPoints(30), backgroundColor: "rgba(231,76,60,0.35)", stack: "zones", order: 2, barThickness: 36, borderWidth: 0},
                    {type: "scatter", label: "Items", data: scatterData, pointRadius: 4.5, pointHoverRadius: 7, backgroundColor: "#111", borderColor: "#111", order: 1}
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: {padding: {top: 24}},
                interaction: {mode: "nearest", intersect: false},
                plugins: {
                    legend: {position: "bottom"},
                    tooltip: {enabled: false, external: externalTooltipHandler},
                    countPluginWia: {statuses, counts}
                },
                onClick: (event) => {
                    const points = wiaChart.getElementsAtEventForMode(event, "nearest", {intersect: false}, true);
                    if (!points.length) return;
                    const point = points[0];
                    const dataset = wiaChart.data.datasets[point.datasetIndex];
                    if (dataset.type !== "scatter") return;
                    const raw = dataset.data[point.index];
                    if (raw?.url && raw.url !== "#") window.open(raw.url, "_blank", "noopener,noreferrer");
                },
                onHover: (event, elements, chart) => {
                    chart.canvas.style.cursor = elements.length ? "pointer" : "default";
                },
                scales: {
                    x: {
                        type: "linear",
                        min: -0.6,
                        max: Math.max(0, statuses.length - 0.4),
                        ticks: {stepSize: 1, callback: (value) => statuses[value] ?? ""},
                        grid: {display: false}
                    },
                    y: {
                        stacked: true,
                        beginAtZero: true,
                        max: maxY,
                        title: {display: true, text: `${i18n.work_item_age || "Work Item Age"} (${i18n.days || "days"})`}
                    }
                }
            }
        });

        if (!canvas.dataset.tooltipBound) {
            canvas.addEventListener("mouseleave", hideTooltip);
            canvas.addEventListener("touchend", hideTooltip);
            canvas.dataset.tooltipBound = "1";
        }

        const topLink = document.getElementById("wiaOpenJiraTop");
        const cardLink = document.getElementById("wiaOpenJiraCard");
        const openWiaUrl = links?.open_jira_wia || wia?.open_jira_url || "#";
        if (topLink) topLink.href = openWiaUrl;
        if (cardLink) cardLink.href = openWiaUrl;

        const hint = document.getElementById("wiaHint");
        if (hint) {
            hint.textContent = statuses.length ? `${statuses.length} ${i18n.status || "status"} / ${items.length} ${i18n.items || "items"}` : "-";
        }
    }

    let dataTable = null;

    function renderDataTable(items, links) {
        const sorted = [...(items || [])].sort((a, b) => safeNum(b.ageDays, 0) - safeNum(a.ageDays, 0));
        const agingLink = document.getElementById("agingOpenJira");
        if (agingLink) agingLink.href = links?.open_jira_aging || "#";

        const hint = document.getElementById("tableHint");
        if (hint) hint.textContent = `${sorted.length} ${i18n.items || "items"}`;

        if (dataTable) {
            dataTable.destroy();
            $("#agingTable tbody").empty();
        }

        dataTable = $("#agingTable").DataTable({
            data: sorted,
            responsive: true,
            pageLength: 10,
            lengthMenu: [10, 25, 50, 100],
            order: [[3, "desc"]],
            language: {url: page.datatableLanguageUrl},
            columns: [
                {
                    data: "key",
                    render: (data, type, row) => {
                        if (type !== "display") return data;
                        return `<a class="fw-semibold text-decoration-none mono" href="${row.detail_url || "#"}">${data}</a>`;
                    }
                },
                {data: "title", defaultContent: ""},
                {data: "status", defaultContent: ""},
                {
                    data: "ageDays",
                    className: "text-end",
                    render: (data, type) => {
                        const value = safeNum(data, 0);
                        if (type !== "display") return value;
                        return `<span class="badge ${ageBadgeClass(value)}">${value}</span>`;
                    }
                },
                {data: "assignee", defaultContent: "-"},
                {
                    data: "url",
                    orderable: false,
                    searchable: false,
                    className: "text-end",
                    render: (data, type, row) => `
                        <div class="btn-group btn-group-sm" role="group">
                            <a class="btn btn-outline-primary" href="${row.url || "#"}" target="_blank" rel="noopener noreferrer" title="${i18n.open_in_jira || "Open in Jira"}">
                                <i class="fa-brands fa-jira"></i>
                            </a>
                            <a class="btn btn-outline-secondary" href="${row.detail_url || "#"}" title="${i18n.view_details || "View details"}">
                                <i class="fa-solid fa-up-right-from-square"></i>
                            </a>
                        </div>`
                }
            ]
        });
    }

    async function render() {
        reloadButton?.setAttribute("disabled", "disabled");
        try {
            const summary = await loadSummary();
            const itemsPayload = await loadItems();
            const links = summary?.links || {};
            renderKpis(summary?.kpis || {});
            renderBottlenecks(summary?.bottlenecks || [], links);
            buildWiaChart(summary?.wia || {}, links);
            renderDataTable(itemsPayload?.items || [], links);
            setBadge(badgeSummary, i18n.ok_api || "OK (API)", "success");
        } finally {
            reloadButton?.removeAttribute("disabled");
        }
    }

    reloadButton?.addEventListener("click", render);
    render();
})();
