"use strict";

// Page navigation by URL hash (#/overview, #/holdings, ...). Every page is already in index.html; this only
// shows one, marks its link as current, sets the title and moves focus to the page heading. No data is fetched here.
(function () {
  const PAGES = ["overview", "holdings", "performance", "imports", "settings"];
  const TITLES = { overview: "Overview", holdings: "Holdings", performance: "Performance", imports: "Imports", settings: "Settings" };
  const KEY = "portfolio.lastPage";  // only a page name: nothing about the portfolio
  const route = (hash) => window.PortfolioTools.route(hash, PAGES);

  function remembered() {
    try { const v = localStorage.getItem(KEY); return PAGES.includes(v) ? v : null; } catch (e) { return null; }
  }
  function remember(page) {
    try { localStorage.setItem(KEY, page); } catch (e) { /* private mode: nothing to remember */ }
  }

  function init(onShow) {
    const show = (page, moveFocus) => {
      for (const node of document.querySelectorAll(".page")) node.hidden = node.dataset.page !== page;
      for (const link of document.querySelectorAll("[data-nav]")) {
        if (link.dataset.nav === page) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      }
      document.title = `${TITLES[page]} · Portfolio`;
      remember(page);
      if (moveFocus) {
        window.scrollTo(0, 0);
        document.getElementById(`${page}-title`).focus();
      }
      onShow(page);  // charts measure their width, so they redraw once their page is visible
    };

    window.addEventListener("hashchange", () => {
      const page = route(location.hash);
      if (page) show(page, true);
    });
    document.getElementById("skip-link").addEventListener("click", (event) => {
      event.preventDefault();  // a #main link would otherwise change the hash
      document.getElementById("main").focus();
    });

    const first = route(location.hash) || remembered() || "overview";
    if (route(location.hash) !== first) history.replaceState(null, "", `#/${first}`);
    show(first, false);
  }

  window.Nav = { init, PAGES };
})();
