(function () {
    "use strict";

    const dataEl = document.getElementById("sparkline-data");
    if (!dataEl) {
        return;
    }

    let rows;
    try {
        rows = JSON.parse(dataEl.textContent);
    } catch (e) {
        return;
    }

    const compact = dataEl.dataset.compact === "1";
    const H = compact ? 28 : 24;
    const PAD = 1;
    const SVG_NS = "http://www.w3.org/2000/svg";

    const COLORS = {
        views: getComputedStyle(document.documentElement).getPropertyValue("--views").trim() || "#3fb950",
        clones: getComputedStyle(document.documentElement).getPropertyValue("--clones").trim() || "#58a6ff",
    };

    function pointsFor(values, width, height) {
        const max = Math.max(...values, 1);
        const step = (width - PAD * 2) / Math.max(values.length - 1, 1);
        return values.map(function (v, i) {
            const x = PAD + i * step;
            const y = height - PAD - (v / max) * (height - PAD * 2);
            return x + "," + y;
        }).join(" ");
    }

    function overlayPoints(views, clones, width, height) {
        const max = Math.max(...views, ...clones, 1);
        const step = (width - PAD * 2) / Math.max(views.length - 1, 1);
        function series(values) {
            return values.map(function (v, i) {
                const x = PAD + i * step;
                const y = height - PAD - (v / max) * (height - PAD * 2);
                return x + "," + y;
            }).join(" ");
        }
        return { views: series(views), clones: series(clones) };
    }

    function addPolyline(svg, pointStr, series) {
        const poly = document.createElementNS(SVG_NS, "polyline");
        poly.setAttribute("fill", "none");
        poly.setAttribute("stroke", COLORS[series] || COLORS.views);
        poly.setAttribute("stroke-width", "1.5");
        poly.setAttribute("stroke-linejoin", "round");
        poly.setAttribute("stroke-linecap", "round");
        poly.setAttribute("vector-effect", "non-scaling-stroke");
        poly.setAttribute("data-series", series);
        poly.setAttribute("points", pointStr);
        svg.appendChild(poly);
    }

    function renderSingle(slot, values, series) {
        if (!slot || !values || !values.length) {
            return;
        }
        const width = Math.max(slot.clientWidth, 48);
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("class", "sparkline");
        svg.setAttribute("width", "100%");
        svg.setAttribute("height", String(H));
        svg.setAttribute("viewBox", "0 0 " + width + " " + H);
        svg.setAttribute("preserveAspectRatio", "none");
        svg.setAttribute("aria-hidden", "true");
        addPolyline(svg, pointsFor(values, width, H), series);
        slot.replaceChildren(svg);
    }

    function renderOverlay(slot, views, clones) {
        if (!slot || !views || !views.length) {
            return;
        }
        const width = Math.max(slot.clientWidth, 48);
        const pts = overlayPoints(views, clones || [], width, H);
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("class", "sparkline sparkline-overlay");
        svg.setAttribute("width", "100%");
        svg.setAttribute("height", String(H));
        svg.setAttribute("viewBox", "0 0 " + width + " " + H);
        svg.setAttribute("preserveAspectRatio", "none");
        svg.setAttribute("aria-hidden", "true");
        addPolyline(svg, pts.views, "views");
        addPolyline(svg, pts.clones, "clones");
        slot.replaceChildren(svg);
    }

    for (const row of rows) {
        const tile = document.querySelector('.tile[data-repo="' + row.full_name + '"]');
        if (!tile) {
            continue;
        }
        if (compact) {
            renderOverlay(tile.querySelector(".sparkline-chart-overlay"), row.views, row.clones);
        } else {
            renderSingle(tile.querySelector('.sparkline-chart[data-series="views"]'), row.views, "views");
            renderSingle(tile.querySelector('.sparkline-chart[data-series="clones"]'), row.clones, "clones");
        }
    }
})();