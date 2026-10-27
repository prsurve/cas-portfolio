"""Rules for the page's own files that the strict CSP and the chart palette depend on."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "portfolio" / "static"
JS = [STATIC / "app.js", STATIC / "tools.js"]

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
