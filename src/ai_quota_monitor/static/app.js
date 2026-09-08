(function () {
  function parseInterval(trigger) {
    const match = trigger.match(/every\s+(\d+)s/);
    if (!match) {
      return null;
    }
    return Number(match[1]) * 1000;
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
        }
        return;
      }
      element.innerHTML = html;
      element.dataset.refreshState = "idle";
      initialize(element);
    } catch {
      element.dataset.refreshState = "error";
    }
  }

  function initialize(root) {
    root.querySelectorAll("[hx-get]").forEach((element) => {
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

  window.addEventListener("DOMContentLoaded", () => initialize(document));
})();
