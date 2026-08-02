(function () {
  const pathInput = document.getElementById("local-path-input");
  const browseBtn = document.getElementById("local-browse-btn");
  const findBtn = document.getElementById("local-find-btn");
  const browser = document.getElementById("local-browser");
  const listEl = document.getElementById("local-browser-list");
  const pathEl = document.getElementById("local-browser-path");
  const msgEl = document.getElementById("local-browser-msg");
  const upBtn = document.getElementById("local-browser-up");

  if (!pathInput || !browseBtn || !browser) {
    return;
  }

  let currentPath = null;

  function showMsg(text) {
    if (!msgEl) {
      return;
    }
    if (text) {
      msgEl.hidden = false;
      msgEl.textContent = text;
    } else {
      msgEl.hidden = true;
      msgEl.textContent = "";
    }
  }

  function renderListing(data) {
    currentPath = data.path || null;
    if (pathEl) {
      pathEl.textContent = data.path || "(roots)";
    }
    if (upBtn) {
      upBtn.hidden = !data.path;
      upBtn.dataset.parent = data.parent || "";
    }
    listEl.innerHTML = "";
    showMsg(data.error || "");
    if (!data.entries || !data.entries.length) {
      if (!data.error) {
        showMsg("Empty directory");
      }
      return;
    }
    data.entries.forEach(function (entry) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "local-browser-item" + (entry.is_git ? " is-git" : "");
      btn.textContent = entry.name + (entry.is_git ? "  · git" : "");
      btn.addEventListener("click", function () {
        pathInput.value = entry.path;
        if (entry.is_git) {
          return;
        }
        loadBrowse(entry.path);
      });
      li.appendChild(btn);
      listEl.appendChild(li);
    });
  }

  function loadBrowse(path) {
    browser.hidden = false;
    showMsg("Loading…");
    listEl.innerHTML = "";
    const qs = path ? "?path=" + encodeURIComponent(path) : "";
    fetch("/api/local/browse" + qs, { credentials: "same-origin" })
      .then(function (res) {
        return res.json();
      })
      .then(renderListing)
      .catch(function () {
        showMsg("Failed to list directory");
      });
  }

  browseBtn.addEventListener("click", function () {
    if (!browser.hidden && !pathInput.value) {
      browser.hidden = true;
      return;
    }
    loadBrowse(pathInput.value || null);
  });

  if (upBtn) {
    upBtn.addEventListener("click", function () {
      const parent = upBtn.dataset.parent;
      if (parent) {
        loadBrowse(parent);
      } else {
        loadBrowse(null);
      }
    });
  }

  if (findBtn) {
    findBtn.addEventListener("click", function () {
      const fullName = findBtn.dataset.fullName;
      if (!fullName) {
        return;
      }
      findBtn.disabled = true;
      fetch("/api/local/find?full_name=" + encodeURIComponent(fullName), {
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          findBtn.disabled = false;
          const matches = data.matches || [];
          if (!matches.length) {
            showMsg("No matching git remote under local roots");
            browser.hidden = false;
            return;
          }
          pathInput.value = matches[0];
          showMsg(
            matches.length === 1
              ? "Found match"
              : "Found " + matches.length + " matches; using first"
          );
          browser.hidden = false;
        })
        .catch(function () {
          findBtn.disabled = false;
          showMsg("Find failed");
          browser.hidden = false;
        });
    });
  }
})();
