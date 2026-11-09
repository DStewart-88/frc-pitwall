#!/bin/sh
# Install Pitwall for this user on Linux: a `pitwall` command and an app-menu entry.
#
#   ./install-linux.sh              install (or reinstall) from this folder
#   ./install-linux.sh --uninstall  remove the command and the menu entry (your data stays)
#
# Pitwall runs straight from this folder, so `git pull` here is all an update needs.
# Nothing needs root, and nothing outside your home folder is changed.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
BIN="$HOME/.local/bin"
APPS="$DATA_HOME/applications"
ICONS="$DATA_HOME/icons/hicolor/scalable/apps"

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN/pitwall" "$APPS/pitwall.desktop" "$ICONS/pitwall.svg"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
    echo "Pitwall removed from the app menu. Your robots, events and logs are still in $DATA_HOME/pitwall"
    exit 0
fi

PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
    echo "Pitwall needs Python 3.11 or newer. Install it with: sudo apt install python3" >&2
    exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "Pitwall needs Python 3.11 or newer; $PY is $("$PY" -V 2>&1)." >&2
    exit 1
fi
if [ ! -f "$REPO/pitwall/__init__.py" ]; then
    echo "Run this script from the frc-pitwall folder." >&2
    exit 1
fi

mkdir -p "$BIN" "$APPS" "$ICONS"

cat > "$BIN/pitwall" <<EOF
#!/bin/sh
# Runs Pitwall from $REPO (made by install-linux.sh)
PYTHONPATH="$REPO\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PY" -m pitwall "\$@"
EOF
chmod 755 "$BIN/pitwall"

cp "$REPO/pitwall/app/icon.svg" "$ICONS/pitwall.svg"

cat > "$APPS/pitwall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pitwall
GenericName=Robot log reports
Comment=Turn FRC robot logs into match and event reports
Exec="$BIN/pitwall" app
Icon=pitwall
Terminal=false
Categories=Development;Utility;
Keywords=FRC;robot;logs;wpilog;
StartupNotify=false
EOF
chmod 644 "$APPS/pitwall.desktop"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true

echo "Pitwall $("$BIN/pitwall" --version | cut -d' ' -f2) installed."
echo "  Open it from the app menu (search for Pitwall), or run: pitwall app"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "  Note: $BIN isn't on your PATH yet; log out and back in, or use $BIN/pitwall" ;;
esac
