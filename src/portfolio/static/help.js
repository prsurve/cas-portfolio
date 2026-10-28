"use strict";
// Adds a "?" button to each heading marked data-help="<term>". The button shows or hides a short definition
// from PortfolioTools.GLOSSARY. Text only, built with textContent; nothing is stored.
(function addHelp() {
  const glossary = (typeof PortfolioTools !== "undefined" && PortfolioTools.GLOSSARY) || {};
  document.querySelectorAll("[data-help]").forEach((heading, i) => {
    const text = glossary[heading.dataset.help];
    if (!text) return;
    const note = document.createElement("p");
    note.className = "help-note";
    note.id = `help-note-${i}`;
    note.hidden = true;
    note.textContent = text;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "help-btn";
    button.textContent = "?";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", note.id);
    button.setAttribute("aria-label", `About: ${heading.textContent.trim()}`);
    button.addEventListener("click", () => {
      note.hidden = !note.hidden;
      button.setAttribute("aria-expanded", String(!note.hidden));
    });
    heading.append(" ", button);
    heading.insertAdjacentElement("afterend", note);
  });
})();
