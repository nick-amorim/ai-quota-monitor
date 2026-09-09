(function () {
  const THEME_KEY = "aiQuotaMonitorTheme";

  function parseInterval(trigger) {
    const match = trigger.match(/every\s+(\d+)s/);
    if (!match) {
      return null;
    }
    return Number(match[1]) * 1000;
  }

  function selectWithRoot(root, selector) {
    const elements = [];
    if (root.matches && root.matches(selector)) {
      elements.push(root);
    }
    root.querySelectorAll(selector).forEach((element) => elements.push(element));
    return elements;
  }

  async function refresh(element) {
    if (!element.isConnected) {
      return;
    }

    const url = element.getAttribute("hx-get");
    if (!url) {
      return;
    }

    element.dataset.refreshState = "loading";

    try {
      const response = await fetch(url, {
        headers: { "HX-Request": "true" },
      });
      if (!response.ok) {
        element.dataset.refreshState = "error";
        return;
      }

      const html = await response.text();
      if (element.getAttribute("hx-swap") === "outerHTML") {
        const template = document.createElement("template");
        template.innerHTML = html.trim();
        const replacement = template.content.firstElementChild;
        if (replacement) {
          replacement.dataset.refreshState = "idle";
          element.replaceWith(replacement);
          initialize(replacement);
          initializeDrawers(replacement);
        }
        return;
      }
      element.innerHTML = html;
      element.dataset.refreshState = "idle";
      initialize(element);
      initializeDrawers(element);
    } catch {
      element.dataset.refreshState = "error";
    }
  }

  function initialize(root) {
    selectWithRoot(root, "[hx-get]").forEach((element) => {
      if (element.dataset.hxRefreshReady === "true") {
        return;
      }
      element.dataset.hxRefreshReady = "true";

      const trigger = element.getAttribute("hx-trigger") || "";
      if (trigger.includes("load")) {
        refresh(element);
      }

      const interval = parseInterval(trigger);
      if (interval !== null) {
        const tick = async () => {
          if (!element.isConnected) {
            return;
          }
          await refresh(element);
          window.setTimeout(tick, interval);
        };
        window.setTimeout(tick, interval);
      }
    });
  }

  function applyTheme(theme) {
    const normalized = theme === "light" ? "light" : "dark";
    document.documentElement.classList.remove("dark", "light");
    document.documentElement.classList.add(normalized);
  }

  function initializeTheme() {
    let saved = "dark";
    try {
      saved = window.localStorage.getItem(THEME_KEY) || "dark";
    } catch {
      saved = "dark";
    }
    applyTheme(saved);

    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      if (button.dataset.themeReady === "true") {
        return;
      }
      button.dataset.themeReady = "true";
      button.addEventListener("click", () => {
        const next = document.documentElement.classList.contains("dark") ? "light" : "dark";
        applyTheme(next);
        try {
          window.localStorage.setItem(THEME_KEY, next);
        } catch {
          // Ignore private browsing or locked storage.
        }
      });
    });
  }

  function openDrawer(id) {
    const drawer = document.getElementById(id);
    if (!drawer) {
      return;
    }
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
    const closeButton = drawer.querySelector("button[data-drawer-close]");
    if (closeButton) {
      closeButton.focus();
    }
  }

  function closeDrawer(id) {
    const drawer = document.getElementById(id);
    if (!drawer) {
      return;
    }
    drawer.hidden = true;
    document.body.classList.remove("drawer-open");
  }

  function initializeDrawers(root) {
    selectWithRoot(root, "[data-drawer-open]").forEach((button) => {
      if (button.dataset.drawerReady === "true") {
        return;
      }
      button.dataset.drawerReady = "true";
      button.addEventListener("click", () => openDrawer(button.dataset.drawerOpen));
    });

    selectWithRoot(root, "[data-drawer-close]").forEach((button) => {
      if (button.dataset.drawerCloseReady === "true") {
        return;
      }
      button.dataset.drawerCloseReady = "true";
      button.addEventListener("click", () => closeDrawer(button.dataset.drawerClose));
    });
  }

  function initializeShell() {
    initializeTheme();
    initializeDrawers(document);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") {
        return;
      }
      document.querySelectorAll(".drawer:not([hidden])").forEach((drawer) => {
        closeDrawer(drawer.id);
      });
    });
  }

  window.addEventListener("DOMContentLoaded", () => {
    initializeShell();
    initialize(document);
  });
})();
