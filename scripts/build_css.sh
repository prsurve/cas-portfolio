#!/usr/bin/env bash
# Builds src/portfolio/static/app.css from src/portfolio/static/src/app.src.css with the Tailwind standalone CLI.
# The built file is committed, so running or testing the app needs no Node and no build. Run this after
# changing app.src.css, index.html or a class name in any static/*.js file.
#
#   scripts/build_css.sh           rebuild app.css
#   scripts/build_css.sh --check   rebuild to a temp file and fail if the committed app.css differs (CI)
set -euo pipefail

VERSION="v4.3.3"
# From the release's sha256sums.txt. Update both together when changing VERSION.
SHA_ARM64="55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195"
SHA_X64="dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a"

root="$(cd "$(dirname "$0")/.." && pwd)"
static="$root/src/portfolio/static"
bin="$root/.tools/tailwindcss"

case "$(uname -m)" in
  aarch64|arm64) asset="tailwindcss-linux-arm64"; want="$SHA_ARM64" ;;
  x86_64)        asset="tailwindcss-linux-x64";   want="$SHA_X64" ;;
  *) echo "No pinned Tailwind binary for $(uname -m)" >&2; exit 1 ;;
esac

if [ ! -x "$bin" ] || [ "$(sha256sum "$bin" | cut -d' ' -f1)" != "$want" ]; then
  mkdir -p "$root/.tools"
  echo "Downloading Tailwind $VERSION ($asset)..." >&2
  curl -fL -C - --retry 5 --retry-delay 3 -o "$bin" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/$asset"
  chmod +x "$bin"
  [ "$(sha256sum "$bin" | cut -d' ' -f1)" = "$want" ] || { echo "Checksum mismatch for $asset" >&2; rm -f "$bin"; exit 1; }
fi

out="$static/app.css"
if [ "${1:-}" = "--check" ]; then out="$(mktemp)"; fi
"$bin" --input "$static/src/app.src.css" --output "$out" --minify
if [ "${1:-}" = "--check" ]; then
  cmp -s "$out" "$static/app.css" || { echo "app.css is out of date: run scripts/build_css.sh" >&2; rm -f "$out"; exit 1; }
  rm -f "$out"
fi
