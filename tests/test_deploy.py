"""The systemd unit files: rendered correctly, valid, and never exposing the app beyond localhost."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install_systemd.sh"
UNITS = ["portfolio.service", "portfolio-backup.service", "portfolio-backup.timer",
         "portfolio-prices.service", "portfolio-prices.timer"]


@pytest.fixture
def rendered(tmp_path):
    app = tmp_path / "app"
    (app / ".venv" / "bin").mkdir(parents=True)
    for tool in ("uvicorn", "python"):  # stand-ins: nothing is run, systemd only checks they exist
        exe = app / ".venv" / "bin" / tool
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    shutil.copytree(ROOT / "deploy", app / "deploy")
    out = tmp_path / "units"
    run = subprocess.run(["sh", str(SCRIPT), "--render", str(out)], env={"APP_DIR": str(app), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return app, out


def test_every_unit_is_rendered_with_the_app_path(rendered):
    app, out = rendered
    for name in UNITS:
        text = (out / name).read_text()
        assert "@APP_DIR@" not in text
    assert f"WorkingDirectory={app}" in (out / "portfolio.service").read_text()


def test_the_dashboard_only_listens_on_localhost(rendered):
    _, out = rendered
    exec_line = next(line for line in (out / "portfolio.service").read_text().splitlines()
                     if line.startswith("ExecStart="))
    args = exec_line.split()
    assert args[args.index("--host") + 1] == "127.0.0.1" and args[args.index("--port") + 1] == "8000"
    assert "--factory" in args and "--no-access-log" in args and "portfolio.app:create_app" in args
    for name in UNITS:
        assert "0.0.0.0" not in (ROOT / "deploy" / name).read_text()


def test_the_run_command_matches_the_documented_one():
    docs = (ROOT / "README.md").read_text()
    assert ("portfolio.app:create_app --factory \\\n  --host 127.0.0.1 --port 8000 --no-access-log" in docs)


def test_files_the_services_create_are_private():
    for name in UNITS:
        if name.endswith(".service"):
            text = (ROOT / "deploy" / name).read_text()
            assert "UMask=0077" in text and "NoNewPrivileges=yes" in text


def test_jobs_run_the_right_commands(rendered):
    _, out = rendered
    assert "-m portfolio.backup create" in (out / "portfolio-backup.service").read_text()
    assert "-m portfolio.prices" in (out / "portfolio-prices.service").read_text()
    for kind in ("backup", "prices"):
        timer = (out / f"portfolio-{kind}.timer").read_text()
        assert "Persistent=true" in timer and "WantedBy=timers.target" in timer


def test_prices_are_refreshed_on_indian_market_time(rendered):
    _, out = rendered
    calendars = [line for line in (out / "portfolio-prices.timer").read_text().splitlines()
                 if line.startswith("OnCalendar=")]
    assert len(calendars) == 2 and all(c.endswith("Asia/Kolkata") for c in calendars)


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed")
def test_systemd_accepts_the_units(rendered):
    _, out = rendered
    run = subprocess.run(["systemd-analyze", "--user", "verify", *[str(out / n) for n in UNITS]],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and run.stderr.strip() == "", run.stderr


def test_a_path_with_spaces_is_refused(tmp_path):
    run = subprocess.run(["sh", str(SCRIPT), "--render", str(tmp_path / "o")],
                         env={"APP_DIR": "/tmp/has space", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
    assert run.returncode == 1 and "spaces" in run.stderr


def _fake_tools(tmp_path, listening: bool):
    """Stand-ins for ss, systemctl and loginctl that record what the installer asks for."""
    bin_dir, log = tmp_path / "fakebin", tmp_path / "calls.log"
    bin_dir.mkdir()
    scripts = {
        "ss": "#!/bin/sh\n" + ('echo "LISTEN 0 2048 127.0.0.1:8000 0.0.0.0:*"\n' if listening else "") + "exit 0\n",
        "systemctl": f'#!/bin/sh\necho "systemctl $*" >> "{log}"\n',
        "loginctl": "#!/bin/sh\necho no\n",
    }
    for name, body in scripts.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    return bin_dir, log


def _install(tmp_path, app, listening):
    bin_dir, log = _fake_tools(tmp_path, listening)
    env = {"APP_DIR": str(app), "PATH": f"{bin_dir}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(tmp_path / "cfg"),
           "HOME": str(tmp_path), "USER": "tester"}
    run = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True)
    return run, log


def test_installer_refuses_while_something_holds_port_8000(rendered, tmp_path):
    app, _ = rendered
    run, log = _install(tmp_path, app, listening=True)
    assert run.returncode == 1 and "Port 8000" in run.stderr
    assert not log.exists() and not (tmp_path / "cfg").exists()  # nothing was installed or started


def test_installer_installs_and_starts_the_three_units(rendered, tmp_path):
    app, _ = rendered
    run, log = _install(tmp_path, app, listening=False)
    assert run.returncode == 0, run.stderr
    assert sorted(p.name for p in (tmp_path / "cfg" / "systemd" / "user").iterdir()) == sorted(UNITS)
    calls = log.read_text().splitlines()
    assert calls[0] == "systemctl --user daemon-reload"
    assert calls[1] == ("systemctl --user enable --now portfolio.service portfolio-backup.timer "
                        "portfolio-prices.timer")
    assert "enable-linger tester" in run.stdout  # tells the user about the one sudo step


def test_installer_needs_the_virtualenv(rendered, tmp_path):
    app, _ = rendered
    (app / ".venv" / "bin" / "uvicorn").unlink()
    run, log = _install(tmp_path, app, listening=False)
    assert run.returncode == 1 and "virtualenv" in run.stderr and not log.exists()
