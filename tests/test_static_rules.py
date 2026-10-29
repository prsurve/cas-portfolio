"""Rules for the page's own files that the strict CSP and the chart palette depend on."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "portfolio" / "static"
CSS_SOURCE = STATIC / "src" / "app.src.css"  # the built app.css is generated from it (scripts/build_css.sh)
JS = [STATIC / "app.js", STATIC / "tools.js", STATIC / "nav.js", STATIC / "holdings.js", STATIC / "imports.js", STATIC / "settings.js", STATIC / "theme.js", STATIC / "returns.js", STATIC / "lots.js", STATIC / "confidence.js", STATIC / "help.js", STATIC / "stress.js", STATIC / "drift.js", STATIC / "reports.js", STATIC / "timeline.js"]

# Validated with the dataviz skill's scripts/validate_palette.js against this app's card surfaces
# (adjacent pairs, light #ffffff and dark #1f1f1d). Change a hex and the check must be run again.
SERIES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"],
}


def test_chart_palette_is_the_validated_one():
    css = (CSS_SOURCE).read_text()
    found = re.findall(r"--series-(\d):\s*(#[0-9a-fA-F]{6});", css)
    expected_light = [(str(i + 1), h) for i, h in enumerate(SERIES["light"])]
    expected_dark = [(str(i + 1), h) for i, h in enumerate(SERIES["dark"])]
    # light, dark when the system is dark, dark when forced from Settings: the last two must be identical
    assert found[:4] == expected_light and found[4:8] == expected_dark and found[8:12] == expected_dark
    assert len(found) == 12, "a series colour was added or dropped: validate the palette again"
    # the dark values live inside the dark-mode media query
    dark_block = css[css.index("prefers-color-scheme: dark"):]
    assert all(h in dark_block[:900] for h in SERIES["dark"])


def test_forced_dark_theme_is_a_copy_of_the_system_dark_theme():
    """Dark exists twice (system dark, and forced from Settings). They must never drift apart."""
    css = (CSS_SOURCE).read_text()
    media = css[css.index('@media (prefers-color-scheme: dark)'):css.index(':root[data-theme="dark"]')]
    forced = css[css.index(':root[data-theme="dark"]'):css.index(':root[data-theme="light"]')]
    tokens = lambda block: dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))  # noqa: E731
    assert tokens(media) == tokens(forced) and len(tokens(media)) >= 14
    assert 'color-scheme: dark' in forced
    assert ':root:not([data-theme="light"])' in media, "an explicit light choice must beat a dark system"


def test_script_never_writes_inline_styles_or_markup():
    """The CSP blocks style attributes and inline scripts. Set widths through the CSSOM (element.style.x = ...)."""
    for path in JS:
        text = path.read_text()
        assert "setAttribute(\"style\"" not in text and "setAttribute('style'" not in text, path.name
        assert not re.search(r'(?:el|svgEl)\(\s*"[\w-]+"\s*,\s*\{[^}]*\bstyle\s*:', text), \
            f"{path.name} passes a style attribute to el()"
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
        assert "innerHTML" not in code and "outerHTML" not in code and "insertAdjacentHTML" not in code, path.name
    html = (STATIC / "index.html").read_text()
    assert " style=" not in html and "<style" not in html


# ---- phones --------------------------------------------------------------------------


def test_nothing_can_stretch_the_page_sideways():
    """Two real bugs found at 360px: a grid column with a 340px minimum, and hidden screen-reader text that is
    absolutely positioned and so escaped its table's scroll box (the page measured 610px on a 345px screen)."""
    css = (CSS_SOURCE).read_text()
    assert re.search(r"\.table-wrap\s*\{[^}]*position:\s*relative[^}]*overflow-x:\s*auto", css), \
        ".table-wrap must be position: relative, or .sr-only text inside a wide table widens the page"
    assert "minmax(min(340px, 100%)" in css and "minmax(340px" not in css.replace("min(340px", ""), \
        "a grid column minimum wider than a phone makes the whole page scroll sideways"


def test_every_table_column_has_a_phone_label():
    """On a phone a row becomes a card and each number is labelled from data-label. A new column needs a label."""
    html = (STATIC / "index.html").read_text()
    js = "".join(p.read_text() for p in JS)
    assert "@media (max-width: 640px)" in (CSS_SOURCE).read_text()
    tables = re.findall(r'<table id="(\w+)" class="cards">(.*?)</table>', html, re.S)
    assert {t[0] for t in tables} == {"history", "changes", "holdings", "statements", "audit", "backups", "returns", "lots", "figures", "stress", "drift", "snapshots", "timeline"}
    for table_id, body in tables:
        headings = re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)
        for index, raw in enumerate(headings):
            text = re.sub(r"<[^>]+>", "", raw).strip()
            if index == 0 or not text or text == "Actions":  # the first column is the card's title; actions are buttons
                continue
            assert f'"{text}"' in js, f"table #{table_id}: column {text!r} has no data-label in app.js"


def test_every_kind_of_control_has_a_visible_focus_ring():
    """Keyboard users need to see where they are: links, buttons, inputs, selects, summaries and focusable rows."""
    css = (CSS_SOURCE).read_text()
    rule = re.search(r"([^{}]*:focus-visible[^{}]*)\{[^}]*outline", css, re.S)
    assert rule, "no :focus-visible outline rule"
    for selector in ("a:focus-visible", "button:focus-visible", "input:focus-visible", "select:focus-visible",
                     "summary:focus-visible", "[tabindex]:focus-visible"):
        assert selector in rule.group(1), f"no focus ring for {selector}"


def test_screen_reader_text_does_not_wrap():
    assert re.search(r"\.sr-only\s*\{[^}]*white-space:\s*nowrap", (CSS_SOURCE).read_text())


def test_search_boxes_are_debounced():
    js = "".join(p.read_text() for p in JS)
    assert js.count("Tools.debounce(") == 1, "the one search box is debounced"


def _theme_colours():
    """Colour tokens for light and dark, read from app.css."""
    css = (CSS_SOURCE).read_text()
    split = css.index("prefers-color-scheme: dark")
    end = css.index("* { box-sizing")
    token = re.compile(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})")
    light = dict(token.findall(css[:split]))
    return light, {**light, **dict(token.findall(css[split:end]))}


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_colours_meet_wcag_aa_in_both_themes():
    """4.5:1 for normal text. Found in review: white-on-accent was 2.5:1 in dark mode."""
    for name, t in zip(("light", "dark"), _theme_colours()):
        for fg in ("text", "muted", "gain", "loss", "accent"):
            for bg in ("bg", "surface"):
                assert contrast(t[fg], t[bg]) >= 4.5, f"{name}: --{fg} on --{bg}"
        assert contrast(t["warn"], t["warn-bg"]) >= 4.5, f"{name}: --warn on --warn-bg"
        assert contrast(t["on-accent"], t["accent"]) >= 4.5, f"{name}: --on-accent on --accent"


def test_allocation_labels_are_readable_on_every_segment_colour():
    css = (CSS_SOURCE).read_text()
    assert re.search(r"\.stack \.seg\s*\{[^}]*color:\s*#000", css), "segment labels must be black on the series colours"
    assert re.search(r"\.stack \.s0\s*\{\s*color:\s*var\(--bg\)", css)
    for name, t in zip(("light", "dark"), _theme_colours()):
        for n in range(1, 5):
            assert contrast("#000000", t[f"series-{n}"]) >= 4.5, f"{name}: label on --series-{n}"
        assert contrast(t["bg"], t["muted"]) >= 4.5, f"{name}: label on the 'other' segment"


def test_there_is_a_tablet_breakpoint_and_big_touch_targets():
    css = (CSS_SOURCE).read_text()
    assert "@media (min-width: 641px) and (max-width: 900px)" in css
    assert re.search(r"min-height:\s*44px", css)


def test_every_element_the_script_looks_up_exists_in_the_page():
    """A renamed or removed id would otherwise fail only in the browser, as a null error."""
    html = (STATIC / "index.html").read_text()
    scripts = "".join(p.read_text() for p in JS)
    ids = set(re.findall(r'id="([\w-]+)"', html)) | set(re.findall(r'\bid: "([\w-]+)"', scripts))  # JS can create ids too
    wanted = set(re.findall(r'getElementById\("([\w-]+)"\)', scripts))
    assert wanted - ids == set(), f"app.js looks up ids that index.html lacks: {sorted(wanted - ids)}"


def test_new_install_shows_steps_and_load_errors_offer_a_retry():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert 'id="empty-state"' in html and 'id="loading"' in html
    assert 'getElementById("empty-state").hidden = !empty' in js
    assert "Try again" in js and "showLoadError(err)" in js
    assert re.search(r'id="upload-result"[^>]*tabindex="-1"', html), "the upload result must be focusable so it can be announced"


def test_shell_has_landmarks_skip_link_and_one_page_per_nav_link():
    html = (STATIC / "index.html").read_text()
    nav = (STATIC / "nav.js").read_text()
    assert html.count("<main") == 1 and 'aria-label="Main"' in html and 'id="skip-link"' in html
    links = re.findall(r'data-nav="(\w+)"', html)
    pages = re.findall(r'class="page" id="page-(\w+)" data-page="\1" hidden', html)
    assert links == pages and len(links) == len(set(links)) >= 5
    declared = re.search(r"const PAGES = \[(.*?)\]", nav).group(1)
    assert re.findall(r'"(\w+)"', declared) == links, "nav.js and index.html disagree about the pages"
    for page in pages:  # each page has its own h1, which receives focus on navigation
        assert f'<h1 id="{page}-title" tabindex="-1">' in html
    assert html.count("<h1") == len(pages), "one h1 per page"
    assert html.index("/static/tools.js") < html.index("/static/nav.js") < html.index("/static/holdings.js") < html.index("/static/imports.js") < html.index("/static/returns.js") < html.index("/static/settings.js") < html.index("/static/app.js")


def test_every_table_and_control_lives_inside_a_page():
    """A section left outside a .page would show on every page."""
    html = (STATIC / "index.html").read_text()
    main = html[html.index("<main"):html.index("</main>")]
    outside = re.sub(r'<div class="page".*?\n    </div>\n', "", main, flags=re.S)
    assert "<section" not in outside and "<table" not in outside


def test_nav_is_a_bottom_bar_on_phones_and_marks_the_current_page_without_colour_alone():
    css = (CSS_SOURCE).read_text()
    assert re.search(r"\.nav a\[aria-current=\"page\"\]\s*\{[^}]*font-weight:\s*600", css)
    assert re.search(r"@media \(max-width: 640px\) \{\s*\.shell \{ display: block; \}.*?\.side \{ position: fixed", css, re.S)


def test_hidden_attribute_always_wins_over_display_rules():
    """Found by looking at the page: .tiles { display: grid } kept the empty install's totals on screen."""
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", (CSS_SOURCE).read_text())


def test_links_use_the_theme_colour():
    assert re.search(r"^a \{ color: var\(--accent\); \}", (CSS_SOURCE).read_text(), re.M)


def test_overview_has_its_panels_and_links_to_the_pages_behind_them():
    html = (STATIC / "index.html").read_text()
    start = html.index('id="page-overview"')
    overview = html[start:html.index('id="page-holdings"')]
    for panel in ("totals", "prices-bar", "fresh-section", "prev-section", "complete-section", "alloc-section", "conc-section", "recent-section"):
        assert f'id="{panel}"' in overview, f"Overview lost its {panel} panel"
    for target in ("#/imports", "#/holdings", "#/performance"):
        assert f'href="{target}"' in overview, f"Overview has no link to {target}"
    # headline first, then what needs attention, what changed, the mix, and where the data comes from last
    order = ["totals", "prices-bar", "complete-section", "prev-section", "alloc-section", "conc-section", "recent-section", "fresh-section"]
    assert [overview.index(f'id="{p}"') for p in order] == sorted(overview.index(f'id="{p}"') for p in order)


def test_data_quality_shows_what_each_issue_affects_and_what_to_do():
    js = (STATIC / "confidence.js").read_text()
    assert '"Affects: "' in js and '"What to do: "' in js, "an issue must say what it affects and what to do"
    assert "r.issues.map(issueItem)" in js and "r.passed.map(" in js, "issues listed first, passing checks folded away"
    assert 'id="completeness-ok"' in (STATIC / "index.html").read_text()
    for status in ("Fully backed", "Partly backed", "Cannot be worked out"):
        assert status in js
    assert "STATUS_MARK" in js, "status is a mark and words, not colour alone"


def test_el_flattens_nested_children():
    """A nested array of elements reached the page as '[object HTMLElement]' until el() flattened all levels."""
    assert "children.flat(Infinity)" in (STATIC / "app.js").read_text()


def test_nothing_that_can_be_null_is_passed_straight_to_replaceChildren():
    """replaceChildren(null) prints the word 'null'. Optional pieces go inside el(), which skips null."""
    js = (STATIC / "holdings.js").read_text()
    detail = js[js.index("function renderHoldingDetail"):js.index("// Shows the list")]
    assert detail.count("replaceChildren(el(") == 1, "the detail view builds one el() wrapper"


def test_import_and_holding_views_never_pass_optional_values_straight_to_replaceChildren():
    """A ternary that can give null as a direct argument prints the word 'null' on the page (seen twice)."""
    for name in ("imports.js", "holdings.js"):
        js = (STATIC / name).read_text()
        for call in re.finditer(r"replaceChildren\(\n(?:[^()]|\([^()]*\))*?: null,?\n", js):
            raise AssertionError(f"{name}: replaceChildren with a possibly-null argument near: {call.group(0)[:80]!r}")


def test_theme_is_applied_from_the_head_before_the_page_is_drawn():
    html = (STATIC / "index.html").read_text()
    assert html.index("/static/theme.js") < html.index("</head>"), "theme.js must load in <head> so there is no flash of the wrong theme"
    js = (STATIC / "theme.js").read_text()
    assert 'VALID = ["system", "light", "dark"]' in js and "portfolio.theme" in js


def test_delete_all_uses_a_dialog_not_a_prompt_and_stays_disabled_until_the_phrase_matches():
    html, js = (STATIC / "index.html").read_text(), "".join(p.read_text() for p in JS)
    assert "<dialog" in html and "prompt(" not in js
    assert re.search(r'id="delete-confirm"[^>]*\bdisabled\b', html)
    assert "delete all data" in js and "showModal()" in js


def test_danger_button_text_is_readable_in_both_themes():
    for name, t in zip(("light", "dark"), _theme_colours()):
        assert contrast(t["bg"], t["loss"]) >= 4.5, f"{name}: danger button text"


def test_returns_section_states_its_limits_and_asks_only_when_shown():
    html, js = (STATIC / "index.html").read_text(), (STATIC / "returns.js").read_text()
    section = html[html.index('id="returns-section"'):html.index('id="changes-section"')]
    for phrase in ("whole holding", "never estimated", "not comparable", "not tax advice"):
        assert phrase in section, f"the Returns section must say: {phrase}"
    assert 'document.getElementById("page-performance").hidden' in js, "returns are worked out only while the page shows"
    assert "lastReturnsKey = key;  // only a success is remembered" in js
    assert 'r.status === "ok" ? null' in js and "r.reason" in js, "a skipped fund shows its reason"


def test_long_text_in_the_returns_table_wraps():
    """A skipped fund's reason is a sentence. Unwrapped, it pushed every other column off the screen at desktop width."""
    assert re.search(r"#returns td:first-child\s*\{[^}]*white-space:\s*normal", (CSS_SOURCE).read_text())


def test_the_page_stores_only_display_settings_in_the_browser():
    """Remembered things are settings under the 'portfolio.' prefix. Holdings, amounts and search text never go in."""
    scripts = {p.name: p.read_text() for p in JS}
    for name, js in scripts.items():
        for call in re.findall(r"(?:localStorage|sessionStorage)\.(?:setItem|getItem)\(([^)]*)\)", js):
            assert call.startswith(("KEY", "\"portfolio.")), f"{name}: storage key outside the portfolio. prefix: {call}"
    holdings = scripts["holdings.js"]
    assert 'prefs.set("holdingsView", viewState())' in holdings
    view = re.search(r"const viewState = \(\) => \(\{(.*?)\}\);", holdings).group(1)
    assert "query" not in view, "the search text must not be part of what is remembered or saved"
    assert "cleanView(" in holdings, "values read back from storage are checked"


def test_saved_views_have_a_dialog_with_an_error_area():
    html = (STATIC / "index.html").read_text()
    assert 'id="view-dialog"' in html and 'id="view-error" role="alert"' in html and 'maxlength="40"' in html


def test_tablet_shell_override_comes_after_the_base_shell_rule():
    """An earlier media rule silently lost to the later base rule (found by measuring the page at 900px)."""
    css = (CSS_SOURCE).read_text()
    base = css.index(".shell { display: grid; grid-template-columns: 176px")
    tablet = css.rindex("@media (min-width: 641px) and (max-width: 900px)")
    assert tablet > base and ".shell { grid-template-columns: 132px" in css[tablet:]


def test_headings_that_receive_focus_do_not_show_a_ring():
    css = (CSS_SOURCE).read_text()
    assert re.search(r'h1\[tabindex="-1"\]:focus[^{]*\{\s*outline:\s*none', css)
    assert "[tabindex]:focus-visible" in css, "other focusable things keep their ring"


def test_help_terms_all_have_a_definition_and_a_heading():
    """Every data-help in the page names a glossary entry, and help.js is loaded after the headings exist."""
    html = (STATIC / "index.html").read_text()
    tools = (STATIC / "tools.js").read_text()
    glossary = re.search(r"const GLOSSARY = \{(.*?)\n  \};", tools, re.S).group(1)
    keys = set(re.findall(r"^    (\w+):", glossary, re.M))
    used = re.findall(r'<h2 [^>]*data-help="(\w+)"', html)
    assert used and set(used) <= keys, f"data-help without a glossary entry: {set(used) - keys}"
    assert html.index("/static/app.js") < html.index("/static/help.js")
    assert "innerHTML" not in (STATIC / "help.js").read_text()


def test_every_money_tile_carries_a_date():
    """F-03: the invested, gain and change tiles say which date they are as of, like the value tiles do."""
    js = (STATIC / "app.js").read_text()
    for label in ('tile("Mutual funds invested", money(t.mf_cost), mfDate)', "latest price as of ${day(m.as_of)}", "${pct(t.mf_gain_pct)}, ${mfDate}", "concAsOf"):
        assert label in js, f"missing date on a figure: {label}"


def test_history_by_class_table_labels_every_value_cell():
    """The by-asset-class table is built in JS, so the card layout's data-label has to be set there."""
    js = (STATIC / "app.js").read_text()
    body = js[js.index("function renderHistoryClasses"):js.index("function renderHistory(")]
    assert body.count('"data-label": l') == 2


def test_lots_section_states_its_limits_and_asks_only_when_shown():
    html, js = (STATIC / "index.html").read_text(), (STATIC / "lots.js").read_text()
    section = html[html.index('id="lots-section"'):html.index('id="changes-section"')]
    for phrase in ("whole holding", "never estimated", "not tax advice", "oldest lots first"):
        assert phrase in section, f"the Lots section must say: {phrase}"
    assert 'document.getElementById("page-performance").hidden' in js, "lots are worked out only while the page shows"
    assert "lastLotsKey = key;  // only a success is remembered" in js
    assert 'f.status === "ok" ? null' in js and "f.reason" in js, "a skipped fund shows its reason"
    assert re.search(r"#lots td:first-child\s*\{[^}]*white-space:\s*normal", (CSS_SOURCE).read_text())


# ---- the Tailwind build ---------------------------------------------------------------


def test_built_stylesheet_is_present_licensed_and_has_the_components_the_page_uses():
    """app.css is generated from app.src.css and committed (CI rebuilds it and fails if it differs). The page loads
    only the built file, and Tailwind's MIT notice must stay at its top."""
    built = (STATIC / "app.css").read_text()
    assert built.startswith("/*! tailwindcss v") and "MIT License" in built.splitlines()[0]
    assert "@import" not in built and "@source" not in built, "app.css is the build output, not the source"
    for selector in (".btn", ".card", ".tile", ".table-wrap", ".stack", ".nav", ".sr-only", "table.cards"):
        assert selector in built, f"{selector} missing from app.css: run scripts/build_css.sh"
    assert len(built) < 120_000, "the stylesheet should stay small for a Raspberry Pi"
    assert '/static/app.css"' in (STATIC / "index.html").read_text()


def test_no_utility_class_is_assembled_from_pieces():
    """Tailwind only generates a class it can read as a whole word in index.html or a script. Colours and sizes
    chosen at run time must come from a map of full class names."""
    for path in JS:
        for line in path.read_text().splitlines():
            if line.lstrip().startswith("//"):
                continue
            assert not re.search(r"""class(?:Name)?\s*[:=]\s*[`"'][^`"']*(?:bg|text|border|grid-cols|gap|p|m)-[`"']\s*\+""", line), \
                f"{path.name}: a utility class built by concatenation: {line.strip()[:80]}"
