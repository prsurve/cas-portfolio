"use strict";

// Page navigation by URL hash (#/overview, #/holdings, ...). Every page is already in index.html; this only
// shows one, marks its link as current, sets the title and moves focus to the page heading. No data is fetched here.
(function () {
  const PAGES = ["overview", "holdings", "performance", "imports", "reports", "settings"];
  const TITLES = { overview: "Overview", holdings: "Holdings", performance: "Performance", imports: "Imports", reports: "Reports", settings: "Settings" };
  const KEY = "portfolio.lastPage";  // only a page name: nothing about the portfolio
  const route = (hash) => window.PortfolioTools.parseRoute(hash, PAGES);

  function remembered() {
    try { const v = localStorage.getItem(KEY); return PAGES.includes(v) ? v : null; } catch (e) { return null; }
  }
  function remember(page) {
    try { localStorage.setItem(KEY, page); } catch (e) { /* private mode: nothing to remember */ }
  }

  function init(onShow) {
    const show = (page, arg, moveFocus) => {
      for (const node of document.querySelectorAll(".page")) node.hidden = node.dataset.page !== page;
      for (const link of document.querySelectorAll("[data-nav]")) {
        if (link.dataset.nav === page) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      }
      document.title = `${TITLES[page]} · Portfolio`;
      remember(page);
      if (moveFocus) window.scrollTo(0, 0);
      // A page with a detail view (#/holdings/<isin>) moves focus to the detail heading once it has loaded.
      if (moveFocus && !arg) document.getElementById(`${page}-title`).focus();
      onShow(page, arg, moveFocus);  // charts measure their width, so they redraw once their page is visible
    };

    initTheme();
    // In-page jumps (the Performance page is long): scroll to the section and move focus to its heading.
    for (const button of document.querySelectorAll("[data-jump]")) {
      const target = document.getElementById(button.dataset.jump);
      // A section that has nothing to show is hidden by the script that owns it; its link follows.
      const sync = () => { button.hidden = target.hidden; };
      new MutationObserver(sync).observe(target, { attributes: true, attributeFilter: ["hidden"] });
      sync();
      button.addEventListener("click", () => {
        const section = document.getElementById(button.dataset.jump);
        if (!section || section.hidden) return;
        const heading = section.querySelector("h2");
        section.scrollIntoView({ block: "start" });
        heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: true });
      });
    }
    window.addEventListener("hashchange", () => {
      const found = route(location.hash);
      if (found) show(found.page, found.arg, true);
    });
    document.getElementById("skip-link").addEventListener("click", (event) => {
      event.preventDefault();  // a #main link would otherwise change the hash
      document.getElementById("main").focus();
    });

    const found = route(location.hash);
    const first = found ? found.page : remembered() || "overview";
    if (!found) history.replaceState(null, "", `#/${first}`);
    show(first, found ? found.arg : null, false);
  }

  // The header shows which date the numbers are from on every page. A mixed range is a warning, so it says so in words.
  function setAsOf(text, mixed) {
    const node = document.getElementById("asof");
    node.textContent = text ? (mixed ? `${text} (statements of different dates)` : text) : "";
    node.classList.toggle("mixed", Boolean(mixed));
  }

  // One button cycles System, Light, Dark; Settings has the same choice as radio buttons.
  function initTheme() {
    const button = document.getElementById("theme-toggle"), label = document.getElementById("theme-label");
    const NAMES = { system: "Auto", light: "Light", dark: "Dark" };
    const paint = () => {
      const current = window.Theme.get();
      label.textContent = NAMES[current];
      button.setAttribute("aria-label", `Theme: ${NAMES[current]}. Change theme`);
    };
    button.addEventListener("click", () => {
      const order = window.Theme.VALID;
      window.Theme.set(order[(order.indexOf(window.Theme.get()) + 1) % order.length]);
      paint();
    });
    window.addEventListener("themechange", paint);
    paint();
  }

  window.Nav = { init, PAGES, setAsOf, initTheme };
})();
