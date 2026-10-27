"""Rules for the page's own files that the strict CSP and the chart palette depend on."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "portfolio" / "static"
JS = [STATIC / "app.js", STATIC / "tools.js", STATIC / "nav.js"]

# Validated with the dataviz skill's scripts/validate_palette.js against this app's card surfaces
# (adjacent pairs, light #ffffff and dark #1f1f1d). Change a hex and the check must be run again.
SERIES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
    "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"],
}


def test_chart_palette_is_the_validated_one():
    css = (STATIC / "app.css").read_text()
    found = re.findall(r"--series-(\d):\s*(#[0-9a-fA-F]{6});", css)
    assert [(n, h) for n, h in found[:4]] == [(str(i + 1), h) for i, h in enumerate(SERIES["light"])]
    assert [(n, h) for n, h in found[4:8]] == [(str(i + 1), h) for i, h in enumerate(SERIES["dark"])]
    assert len(found) == 8, "a series colour was added or dropped: validate the palette again"
    # the dark values live inside the dark-mode media query
    dark_block = css[css.index("prefers-color-scheme: dark"):]
    assert all(h in dark_block[:600] for h in SERIES["dark"])


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
    css = (STATIC / "app.css").read_text()
    assert re.search(r"\.table-wrap\s*\{[^}]*position:\s*relative[^}]*overflow-x:\s*auto", css), \
        ".table-wrap must be position: relative, or .sr-only text inside a wide table widens the page"
    assert "minmax(min(340px, 100%)" in css and "minmax(340px" not in css.replace("min(340px", ""), \
        "a grid column minimum wider than a phone makes the whole page scroll sideways"


def test_every_table_column_has_a_phone_label():
    """On a phone a row becomes a card and each number is labelled from data-label. A new column needs a label."""
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    assert "@media (max-width: 640px)" in (STATIC / "app.css").read_text()
    tables = re.findall(r'<table id="(\w+)" class="cards">(.*?)</table>', html, re.S)
    assert {t[0] for t in tables} == {"history", "changes", "funds", "stocks", "statements"}
    for table_id, body in tables:
        headings = re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)
        for index, raw in enumerate(headings):
            text = re.sub(r"<[^>]+>", "", raw).strip()
            if index == 0 or not text or text == "Actions":  # the first column is the card's title; actions are buttons
                continue
            assert f'"{text}"' in js, f"table #{table_id}: column {text!r} has no data-label in app.js"


def test_every_kind_of_control_has_a_visible_focus_ring():
    """Keyboard users need to see where they are: links, buttons, inputs, selects, summaries and focusable rows."""
    css = (STATIC / "app.css").read_text()
    rule = re.search(r"([^{}]*:focus-visible[^{}]*)\{[^}]*outline", css, re.S)
    assert rule, "no :focus-visible outline rule"
    for selector in ("a:focus-visible", "button:focus-visible", "input:focus-visible", "select:focus-visible",
                     "summary:focus-visible", "[tabindex]:focus-visible"):
        assert selector in rule.group(1), f"no focus ring for {selector}"


def test_screen_reader_text_does_not_wrap():
    assert re.search(r"\.sr-only\s*\{[^}]*white-space:\s*nowrap", (STATIC / "app.css").read_text())


def test_search_boxes_are_debounced():
    js = (STATIC / "app.js").read_text()
    assert js.count("Tools.debounce(") == 2


def _theme_colours():
    """Colour tokens for light and dark, read from app.css."""
    css = (STATIC / "app.css").read_text()
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
    css = (STATIC / "app.css").read_text()
    assert re.search(r"\.stack \.seg\s*\{[^}]*color:\s*#000", css), "segment labels must be black on the series colours"
    assert re.search(r"\.stack \.s0\s*\{\s*color:\s*var\(--bg\)", css)
    for name, t in zip(("light", "dark"), _theme_colours()):
        for n in range(1, 5):
            assert contrast("#000000", t[f"series-{n}"]) >= 4.5, f"{name}: label on --series-{n}"
        assert contrast(t["bg"], t["muted"]) >= 4.5, f"{name}: label on the 'other' segment"


def test_there_is_a_tablet_breakpoint_and_big_touch_targets():
    css = (STATIC / "app.css").read_text()
    assert "@media (min-width: 641px) and (max-width: 900px)" in css
    assert re.search(r"min-height:\s*44px", css)


def test_every_element_the_script_looks_up_exists_in_the_page():
    """A renamed or removed id would otherwise fail only in the browser, as a null error."""
    html = (STATIC / "index.html").read_text()
    ids = set(re.findall(r'id="([\w-]+)"', html))
    wanted = set(re.findall(r'getElementById\("([\w-]+)"\)', (STATIC / "app.js").read_text()))
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
    assert html.index("/static/tools.js") < html.index("/static/nav.js") < html.index("/static/app.js")


def test_every_table_and_control_lives_inside_a_page():
    """A section left outside a .page would show on every page."""
    html = (STATIC / "index.html").read_text()
    main = html[html.index("<main"):html.index("</main>")]
    outside = re.sub(r'<div class="page".*?\n    </div>\n', "", main, flags=re.S)
    assert "<section" not in outside and "<table" not in outside


def test_nav_is_a_bottom_bar_on_phones_and_marks_the_current_page_without_colour_alone():
    css = (STATIC / "app.css").read_text()
    assert re.search(r"\.nav a\[aria-current=\"page\"\]\s*\{[^}]*font-weight:\s*600", css)
    assert re.search(r"@media \(max-width: 640px\) \{\s*\.shell \{ display: block; \}.*?\.side \{ position: fixed", css, re.S)


def test_hidden_attribute_always_wins_over_display_rules():
    """Found by looking at the page: .tiles { display: grid } kept the empty install's totals on screen."""
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", (STATIC / "app.css").read_text())


def test_links_use_the_theme_colour():
    assert re.search(r"^a \{ color: var\(--accent\); \}", (STATIC / "app.css").read_text(), re.M)
