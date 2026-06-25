(function () {
    function closeOpenMenus() {
        document.querySelectorAll(".period-menu[open]").forEach(function (menu) {
            menu.open = false;
        });
    }

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            closeOpenMenus();
        }
    });

    document.addEventListener("mousedown", function (e) {
        document.querySelectorAll(".period-menu[open]").forEach(function (menu) {
            if (menu.contains(e.target)) {
                return;
            }
            let active = document.activeElement;
            if (active && menu.contains(active) && active.type === "date") {
                return;
            }
            menu.open = false;
        });
    });
})();
