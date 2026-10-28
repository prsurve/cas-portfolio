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

  window.Nav = { init, PAGES };
})();
