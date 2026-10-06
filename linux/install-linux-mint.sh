#!/bin/sh
# One-time setup for photo-pilot on Linux Mint / Ubuntu / Debian.
set -e
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3-venv sane-utils
# Allow scanning without sudo.
sudo usermod -aG scanner,lp "$USER" || true

APP="$HOME/.local/share/photo-pilot"
python3 -m venv "$APP"
"$APP/bin/pip" install --upgrade pip
"$APP/bin/pip" install .
mkdir -p "$HOME/.local/bin"
ln -sf "$APP/bin/photo-pilot" "$HOME/.local/bin/photo-pilot"

echo
echo "Installed: photo-pilot (log out and back in once so the scanner group applies)."
echo "Scanners found now:"
"$APP/bin/photo-pilot" devices || true
echo
echo "No scanner listed? Install Epson's Linux driver for your model, see README (Linux Mint section)."
