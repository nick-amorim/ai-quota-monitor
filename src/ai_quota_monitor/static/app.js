(function () {
  const THEME_KEY = "aiQuotaMonitorTheme";
  const FOCUSABLE_SELECTOR = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  let activeDrawer = null;
  let drawerOpener = null;

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
        scheduleNextRefresh(element);
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
          updateRefreshCountdowns();
        }
        return;
      }
      element.innerHTML = html;
      element.dataset.refreshState = "idle";
      scheduleNextRefresh(element);
      initialize(element);
      initializeDrawers(element);
      updateRefreshCountdowns();
    } catch {
      element.dataset.refreshState = "error";
      scheduleNextRefresh(element);
    }
  }

  function scheduleNextRefresh(element) {
    const interval = parseInterval(element.getAttribute("hx-trigger") || "");
    if (interval !== null) {
      element.dataset.nextRefreshAt = String(Date.now() + interval);
    }
  }

  function initialize(root) {
    selectWithRoot(root, "[hx-get]").forEach((element) => {
      if (element.dataset.hxRefreshReady === "true") {
        return;
      }
      element.dataset.hxRefreshReady = "true";

      const trigger = element.getAttribute("hx-trigger") || "";
      scheduleNextRefresh(element);
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
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const isDark = normalized === "dark";
      const label = isDark ? "Switch to light theme" : "Switch to dark theme";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
      button.setAttribute("aria-pressed", String(isDark));
    });
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

  function focusableElements(root) {
    return selectWithRoot(root, FOCUSABLE_SELECTOR).filter((element) => {
      return element.offsetParent !== null || element === document.activeElement;
    });
  }

  function openDrawer(id, opener) {
    const drawer = document.getElementById(id);
    if (!drawer) {
      return;
    }
    drawerOpener = opener || document.activeElement;
    activeDrawer = drawer;
    drawer.hidden = false;
    document.body.classList.add("drawer-open");
    const focusTargets = focusableElements(drawer);
    if (focusTargets.length > 0) {
      focusTargets[0].focus();
    }
  }

  function closeDrawer(id) {
    const drawer = document.getElementById(id);
    if (!drawer) {
      return;
    }
    drawer.hidden = true;
    document.body.classList.remove("drawer-open");
    activeDrawer = null;
    if (drawerOpener && drawerOpener.isConnected && typeof drawerOpener.focus === "function") {
      drawerOpener.focus();
    }
    drawerOpener = null;
  }

  function trapDrawerFocus(event) {
    if (!activeDrawer || event.key !== "Tab") {
      return;
    }
    const focusTargets = focusableElements(activeDrawer);
    if (focusTargets.length === 0) {
      event.preventDefault();
      return;
    }
    const first = focusTargets[0];
    const last = focusTargets[focusTargets.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function initializeDrawers(root) {
    selectWithRoot(root, "[data-drawer-open]").forEach((button) => {
      if (button.dataset.drawerReady === "true") {
        return;
      }
      button.dataset.drawerReady = "true";
      button.addEventListener("click", () => openDrawer(button.dataset.drawerOpen, button));
    });

    selectWithRoot(root, "[data-drawer-close]").forEach((button) => {
      if (button.dataset.drawerCloseReady === "true") {
        return;
      }
      button.dataset.drawerCloseReady = "true";
      button.addEventListener("click", () => closeDrawer(button.dataset.drawerClose));
    });
  }

  function updateRefreshCountdowns() {
    document.querySelectorAll("[data-refresh-countdown]").forEach((element) => {
      const targetId = element.dataset.refreshTarget;
      const target = targetId ? document.getElementById(targetId) : null;
      const nextRefreshAt = Number(target ? target.dataset.nextRefreshAt : 0);
      if (!target || !nextRefreshAt) {
        element.textContent = "Refresh --";
        return;
      }
      const seconds = Math.max(0, Math.ceil((nextRefreshAt - Date.now()) / 1000));
      element.textContent = `Refresh ${seconds}s`;
    });
  }

  function initializeShell() {
    initializeTheme();
    initializeDrawers(document);
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") {
        trapDrawerFocus(event);
        return;
      }
      document.querySelectorAll(".drawer:not([hidden])").forEach((drawer) => {
        closeDrawer(drawer.id);
      });
    });
    window.setInterval(updateRefreshCountdowns, 1000);
  }

  window.addEventListener("DOMContentLoaded", () => {
    initializeShell();
    initialize(document);
    updateRefreshCountdowns();
  });
})();
