"use strict";

// Light, dark or follow the system. Loaded in <head> so the saved choice is applied before the page is drawn.
// Only the word "light" or "dark" is stored, in this browser; "system" stores nothing.
(function () {
  const KEY = "portfolio.theme";
  const VALID = ["system", "light", "dark"];
  const root = document.documentElement;

  function get() {
    try {
      const v = localStorage.getItem(KEY);
      return VALID.includes(v) ? v : "system";
    } catch (e) { return "system"; }
  }
  // The stylesheet has one dark definition, keyed on data-theme, so "system" is resolved here and followed live.
  const device = window.matchMedia("(prefers-color-scheme: dark)");
  function apply(theme) {
    root.setAttribute("data-theme", theme === "system" ? (device.matches ? "dark" : "light") : theme);
  }
  function set(theme) {
    if (!VALID.includes(theme)) return;
    try {
      if (theme === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, theme);
    } catch (e) { /* private mode: the choice lasts until the page closes */ }
    apply(theme);
    window.dispatchEvent(new Event("themechange"));  // the header button and the Settings radios both follow it
  }

  apply(get());
  device.addEventListener("change", () => { if (get() === "system") apply("system"); });
  window.Theme = { get, set, VALID };
})();
