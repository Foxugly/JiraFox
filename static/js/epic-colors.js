// One colour per epic, shared by the charts that colour issues by epic (Work Item Age).
(function () {
    const PALETTE = ["#2f7de1", "#f0a202", "#2e9d5b", "#c8475a", "#7a5cc7", "#16a3b8",
                     "#e07a1f", "#5b8c2a", "#d14fa0", "#3d5a80", "#a0522d", "#8e9b2f"];
    const NO_EPIC_COLOR = "#8a94a6";

    /**
     * Groups items by their `epic` ({key, name} or null): most items first, "no epic" last.
     * Returns [{key, label, color, items}]; `noEpicLabel` names the group without an epic.
     */
    function groupByEpic(items, noEpicLabel) {
        const groups = new Map();
        items.forEach((item) => {
            const key = item.epic?.key || "";
            if (!groups.has(key)) {
                groups.set(key, {
                    key,
                    label: key ? `${item.epic.name || key} (${key})` : noEpicLabel,
                    items: []
                });
            }
            groups.get(key).items.push(item);
        });
        const sorted = [...groups.values()].sort((a, b) =>
            (a.key === "") - (b.key === "") || b.items.length - a.items.length || a.label.localeCompare(b.label));
        let index = 0;
        sorted.forEach((group) => {
            group.color = group.key ? PALETTE[index++ % PALETTE.length] : NO_EPIC_COLOR;
        });
        return sorted;
    }

    window.JiraFoxEpics = {groupByEpic, PALETTE, NO_EPIC_COLOR};
})();
