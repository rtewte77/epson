#!/bin/sh
# One-time setup on Linux Mint (or Ubuntu/Debian) for the photo-pilot-linux program.
# Put this script next to the downloaded photo-pilot-linux and run:  sh install-linux-mint.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/photo-pilot-linux"
[ -f "$BIN" ] || { echo "Put photo-pilot-linux next to this script first."; exit 1; }

# SANE: the standard Linux scanner layer photo-pilot talks to.
sudo apt-get update
sudo apt-get install -y sane-utils
sudo usermod -aG scanner,lp "$USER" || true

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
install -m 755 "$BIN" "$HOME/.local/bin/photo-pilot"

# Menu entry + desktop icon that open it in a terminal window.
DESKTOP_FILE="$HOME/.local/share/applications/photo-pilot.desktop"
cat > "$DESKTOP_FILE" <<DESK
[Desktop Entry]
Type=Application
Name=Scan photos (photo-pilot)
Comment=Scan old photos into a folder, one file per photo
Exec=$HOME/.local/bin/photo-pilot %F
Icon=scanner
Terminal=true
Categories=Graphics;Scanning;
DESK
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESKTOP_DIR" ]; then
  cp "$DESKTOP_FILE" "$DESKTOP_DIR/" && chmod +x "$DESKTOP_DIR/photo-pilot.desktop"
fi

echo
echo "Done. Log out and back in once, then start 'Scan photos' from the menu or desktop."
echo "Scanners visible right now:"
scanimage -L || true
echo "No Epson listed? Install Epson's Linux driver for the model (see README)."
