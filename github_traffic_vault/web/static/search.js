(function () {
    "use strict";

    const input = document.getElementById("repo-search");
    if (!input) {
        return;
    }

    const tiles = Array.from(document.querySelectorAll(".tile[data-repo]"));
    const empty = document.getElementById("search-empty");

    function fuzzyMatch(haystack, needle) {
        let i = 0;
        const h = haystack.toLowerCase();
        const n = needle.toLowerCase();
        for (const ch of n) {
            i = h.indexOf(ch, i);
            if (i === -1) {
                return false;
            }
            i += 1;
        }
        return true;
    }

    function apply() {
        const q = input.value.trim();
        let visible = 0;
        for (const tile of tiles) {
            const name = tile.getAttribute("data-repo") || "";
            const show = !q || fuzzyMatch(name, q);
            tile.classList.toggle("hidden", !show);
            if (show) {
                visible += 1;
            }
        }
        if (empty) {
            empty.hidden = visible > 0 || !q;
            if (!empty.hidden) {
                empty.textContent = 'No repos match "' + q + '"';
            }
        }
    }

    input.addEventListener("input", apply);
    input.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            input.value = "";
            apply();
            input.blur();
        }
    });
})();