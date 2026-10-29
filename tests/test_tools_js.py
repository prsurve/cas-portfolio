"""Search, sort and CSV helpers for the page. They are plain functions, so node can test them."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src" / "portfolio" / "static"


@pytest.mark.skipif(shutil.which("node") is None and not os.environ.get("CI"), reason="node is not installed")
def test_table_helpers():
    assert shutil.which("node"), "node is required in CI"
    run = subprocess.run(["node", str(ROOT / "tests" / "tools.test.js")], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stdout.strip() == "ok", run.stderr


def test_page_loads_the_helpers_before_the_app():
    html = (STATIC / "index.html").read_text()
    assert (STATIC / "tools.js").is_file()
    assert html.index("/static/tools.js") < html.index("/static/app.js")
    assert "<script>" not in html and "onclick=" not in html  # the strict CSP allows no inline script
