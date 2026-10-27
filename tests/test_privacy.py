"""The repository must never hold personal data (see CLAUDE.md, privacy rule)."""

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("privacy_check", ROOT / "scripts" / "privacy_check.py")
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


def _check(tmp_path, monkeypatch, name, content):
    monkeypatch.setattr(privacy, "ROOT", tmp_path)
    (tmp_path / name).write_text(content)
    return privacy.check_file(name)


def test_tracked_files_are_clean():
    assert subprocess.run(["python", str(ROOT / "scripts" / "privacy_check.py")], cwd=ROOT).returncode == 0


def test_pan_shaped_string_is_flagged(tmp_path, monkeypatch):
    assert _check(tmp_path, monkeypatch, "a.txt", "pan = QWERT9876Z\n") == ["a.txt:1: PAN-shaped string"]  # privacy-ok


def test_long_id_is_flagged(tmp_path, monkeypatch):
    assert len(_check(tmp_path, monkeypatch, "a.txt", "client 1234567890\n")) == 1  # privacy-ok


def test_isin_and_short_numbers_are_fine(tmp_path, monkeypatch):
    assert _check(tmp_path, monkeypatch, "a.txt", "INE009A01021 qty 1450 price 1234567\n") == []


def test_known_fake_values_and_marker_are_allowed(tmp_path, monkeypatch):
    assert _check(tmp_path, monkeypatch, "a.txt", "ABCDE1234F 12345678\nQWERT9876Z  # privacy-ok\n") == []


def test_forbidden_file_types_are_flagged(tmp_path, monkeypatch):
    for name in ("statement.pdf", "portfolio.db", ".env", "data/x.txt"):
        monkeypatch.setattr(privacy, "ROOT", tmp_path)
        assert privacy.check_file(name), name


def test_report_never_echoes_the_value(tmp_path, monkeypatch):
    assert "QWERT9876Z" not in "".join(_check(tmp_path, monkeypatch, "a.txt", "QWERT9876Z\n"))  # privacy-ok
