"""Fail if files about to be committed or pushed look like they hold personal data.

    python scripts/privacy_check.py            # every tracked file
    python scripts/privacy_check.py --staged   # only what is staged (pre-commit hook)

Checks: forbidden file types and paths, PAN-shaped strings, and 8+ digit numbers
(account, folio or client IDs). Put `privacy-ok` on a line to allow a deliberate
made-up value, such as a test fixture.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Made-up values used by the test fixtures. Exact matches only: anything else
# that looks like a PAN or a long ID is still reported.
KNOWN_FAKE = {
    "ABCDE1234F", "9999999999", "12081600", "12345678", "87654321", "11112222",
    "99998888", "55554444", "99887766", "5555444433",
}


ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_NAME = re.compile(r"(\.pdf|\.db|\.gpg|\.sqlite3?)$|(^|/)\.env$", re.IGNORECASE)
FORBIDDEN_DIR = re.compile(r"(^|/)data/")
FAKE = re.compile(r"\b(" + "|".join(sorted(KNOWN_FAKE)) + r")\b")
PAN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
LONG_ID = re.compile(r"\b\d{8,}\b")
ALLOW = "privacy-ok"

def git_files(staged: bool) -> list[str]:
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"] if staged else ["git", "ls-files"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def check_file(rel: str) -> list[str]:
    """Problems found in one file, as short messages. Never echoes the matched text."""
    if FORBIDDEN_NAME.search(rel) or FORBIDDEN_DIR.search(rel):
        return [f"{rel}: this kind of file must never be committed"]
    path = ROOT / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return []
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        line = FAKE.sub("", line)
        if PAN.search(line):
            problems.append(f"{rel}:{n}: PAN-shaped string")
        if LONG_ID.search(line):
            problems.append(f"{rel}:{n}: number with 8 or more digits (account, folio or client ID?)")
    return problems


def main(argv: list[str] | None = None) -> int:
    staged = "--staged" in (argv if argv is not None else sys.argv[1:])
    problems = [p for rel in git_files(staged) for p in check_file(rel)]
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s). Fix them, or add `{ALLOW}` to a line holding a made-up value.",
              file=sys.stderr)
        return 1
    print("privacy check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
