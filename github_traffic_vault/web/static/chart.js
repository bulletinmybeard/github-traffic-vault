// Hand-rolled SVG line chart for the detail page. Four series on one
// axis: views, unique views, clones, unique clones. Solid lines for
// totals, dashed for uniques. Hover anywhere -> guide line + tooltip
// with all four values for that day.

(function () {
    "use strict";

    const dataEl = document.getElementById("chart-data");
    const container = document.getElementById("chart");
    if (!dataEl || !container) {
        return;
    }

    let data;
    try {
        data = JSON.parse(dataEl.textContent);
    } catch (e) {
        return;
    }
    if (!data.length) {
        return;
    }

    const SERIES = [
        { key: "views",     color: "#3fb950", dashed: false, label: "Views" },
        { key: "v_uniques", color: "#3fb950", dashed: true,  label: "Unique views" },
        { key: "clones",    color: "#58a6ff", dashed: false, label: "Clones" },
        { key: "c_uniques", color: "#58a6ff", dashed: true,  label: "Unique clones" },
    ];

    const SVG_NS = "http://www.w3.org/2000/svg";
    const PADDING = { top: 14, right: 18, bottom: 30, left: 38 };
    const HEIGHT = 280;

    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.hidden = true;
    container.appendChild(tooltip);

    const state = {};
    let resizeTimer = null;

    // ---- helpers ----

    const createSVG = (tag, attrs, text) => {
        const el = document.createElementNS(SVG_NS, tag);
        for (const [k, v] of Object.entries(attrs)) {
            el.setAttribute(k, v);
        }
        if (text !== undefined) el.textContent = text;
        return el;
    };

    const clearChildren = (node) => {
        while (node.firstChild) node.removeChild(node.firstChild);
    };

    const niceStep = (max) => {
        if (max <= 4) return 1;
        const pow10 = Math.pow(10, Math.floor(Math.log10(max)));
        const norm = max / pow10;
        if (norm < 1.5) return 0.2 * pow10;
        if (norm < 3)   return 0.5 * pow10;
        if (norm < 7)   return 1 * pow10;
        return 2 * pow10;
    };

    const niceTicks = (max) => {
        if (max <= 1) return [0, 1];
        const step = niceStep(max);
        const top = Math.ceil(max / step) * step;
        const out = [];
        for (let v = 0; v <= top + step / 2; v += step) {
            out.push(Math.round(v * 1e6) / 1e6);
        }
        return out;
    };

    const pickLabelStep = (n, innerW) => {
        const minSpace = 56;
        const maxLabels = Math.max(2, Math.floor(innerW / minSpace));
        return Math.max(1, Math.ceil(n / maxLabels));
    };

    const formatShortDate = (iso) => {
        const p = iso.split("-");
        return p[1] + "/" + p[2];
    };

    const formatLongDate = (iso) => {
        const p = iso.split("-");
        const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        const m = parseInt(p[1], 10);
        return months[m - 1] + " " + p[2] + ", " + p[0];
    };

    const formatInt = (n) => {
        if (n >= 1000) return (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1) + "k";
        return String(n);
    };

    // ---- rendering ----

    const render = () => {
        const W = Math.max(280, container.clientWidth);
        const H = HEIGHT;
        const innerW = W - PADDING.left - PADDING.right;
        const innerH = H - PADDING.top - PADDING.bottom;

        let maxY = 1;
        for (const row of data) {
            for (const s of SERIES) {
                if (row[s.key] > maxY) maxY = row[s.key];
            }
        }
        const ticks = niceTicks(maxY);
        const topY = ticks[ticks.length - 1];

        const x = (idx) => {
            if (data.length === 1) return PADDING.left + innerW / 2;
            return PADDING.left + (idx * innerW) / (data.length - 1);
        };
        const y = (v) => PADDING.top + innerH - (v / topY) * innerH;

        // Clear and rebuild SVG.
        while (container.firstChild && container.firstChild !== tooltip) {
            container.removeChild(container.firstChild);
        }
        if (tooltip.parentNode === container) {
            container.removeChild(tooltip);
        }

        const svg = createSVG("svg", {
            width: W,
            height: H,
            class: "chart-svg",
            role: "img",
            "aria-label": "Daily traffic chart",
        });

        ticks.forEach((t) => {
            svg.appendChild(createSVG("line", {
                x1: PADDING.left, x2: W - PADDING.right,
                y1: y(t), y2: y(t),
                class: "grid",
            }));
            svg.appendChild(createSVG("text", {
                x: PADDING.left - 6, y: y(t) + 3,
                class: "axis-label y",
            }, formatInt(t)));
        });

        const labelStep = pickLabelStep(data.length, innerW);
        data.forEach((d, idx) => {
            if (idx % labelStep !== 0 && idx !== data.length - 1) return;
            svg.appendChild(createSVG("text", {
                x: x(idx), y: H - PADDING.bottom + 16,
                class: "axis-label x",
            }, formatShortDate(d.date)));
        });

        SERIES.forEach((s) => {
            const pts = data.map((d, idx) => `${x(idx)},${y(d[s.key])}`).join(" ");
            const line = createSVG("polyline", {
                points: pts,
                class: "line",
                fill: "none",
                stroke: s.color,
                "stroke-width": "1.6",
                "data-series": s.key,
            });
            if (s.dashed) line.setAttribute("stroke-dasharray", "4 4");
            svg.appendChild(line);

            data.forEach((d, idx) => {
                svg.appendChild(createSVG("circle", {
                    cx: x(idx), cy: y(d[s.key]),
                    r: 2.6,
                    fill: s.color,
                    class: "dot",
                    "data-series": s.key,
                }));
            });
        });

        const guide = createSVG("line", {
            x1: 0, x2: 0,
            y1: PADDING.top, y2: H - PADDING.bottom,
            class: "guide",
            visibility: "hidden",
        });
        svg.appendChild(guide);

        const capture = createSVG("rect", {
            x: PADDING.left, y: PADDING.top,
            width: innerW, height: innerH,
            fill: "transparent",
        });
        svg.appendChild(capture);

        container.appendChild(svg);
        container.appendChild(tooltip);

        state.svg = svg;
        state.guide = guide;
        state.x = x;
        state.W = W;
        state.H = H;
        state.innerW = innerW;
    };

    const fillTooltip = (d) => {
        // Build the tooltip via DOM nodes (no innerHTML) so values are
        // text-only -- no XSS surface, however benign the source.
        clearChildren(tooltip);

        const dateEl = document.createElement("div");
        dateEl.className = "tt-date";
        dateEl.textContent = formatLongDate(d.date);
        tooltip.appendChild(dateEl);

        SERIES.forEach((s) => {
            const row = document.createElement("div");
            row.className = "tt-row";

            const mark = document.createElement("span");
            mark.className = "tt-mark " + (s.dashed ? "dashed" : "solid");
            mark.style.setProperty("--c", s.color);
            if (!s.dashed) mark.style.background = s.color;
            row.appendChild(mark);

            const label = document.createElement("span");
            label.className = "tt-label";
            label.textContent = s.label;
            row.appendChild(label);

            const value = document.createElement("strong");
            value.className = "tt-value";
            value.textContent = String(d[s.key]);
            row.appendChild(value);

            tooltip.appendChild(row);
        });
    };

    const onMove = (e) => {
        if (!state.svg) {
            return;
        }
        const rect = state.svg.getBoundingClientRect();
        const px = e.clientX - rect.left;
        let ratio = (px - PADDING.left) / state.innerW;
        if (ratio < 0) {
            ratio = 0;
        }
        if (ratio > 1) {
            ratio = 1;
        }
        let idx = Math.round(ratio * (data.length - 1));
        if (idx < 0) {
            idx = 0;
        }
        if (idx >= data.length) {
            idx = data.length - 1;
        }

        const d = data[idx];
        const xPos = state.x(idx);
        state.guide.setAttribute("x1", xPos);
        state.guide.setAttribute("x2", xPos);
        state.guide.setAttribute("visibility", "visible");

        fillTooltip(d);
        tooltip.hidden = false;

        const containerRect = container.getBoundingClientRect();
        const tipX = (xPos / state.W) * rect.width;
        let leftPx = tipX + 12;
        if (leftPx + tooltip.offsetWidth + 12 > containerRect.width) {
            leftPx = tipX - tooltip.offsetWidth - 12;
            if (leftPx < 4) {
                leftPx = 4;
            }
        }
        tooltip.style.left = leftPx + "px";
        tooltip.style.top = (e.clientY - containerRect.top + 8) + "px";
    };

    const onLeave = () => {
        if (state.guide) {
            state.guide.setAttribute("visibility", "hidden");
        }
        tooltip.hidden = true;
    };

    const debouncedRender = () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(render, 80);
    };

    container.addEventListener("mousemove", onMove);
    container.addEventListener("mouseleave", onLeave);

    render();

    if (typeof ResizeObserver !== "undefined") {
        new ResizeObserver(debouncedRender).observe(container);
    } else {
        window.addEventListener("resize", debouncedRender);
    }
})();
