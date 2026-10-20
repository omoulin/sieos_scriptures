#!/bin/sh
# Builds Scriptures, installs the program to $PREFIX/bin (default /usr/local/bin,
# using sudo when that isn't writable) and adds it to the desktop's app list for
# the current user. ./install.sh --uninstall removes both again.
set -e
APP_ID=io.github.ldsscripture.Scriptures
ROOT="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${PREFIX:-/usr/local}"
BIN="$PREFIX/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/scriptures"

# Runs a command as root only when $BIN (or the nearest folder above it that
# exists) can't be written as the current user.
as_root() {
    d="$BIN"; while [ ! -e "$d" ]; do d="$(dirname "$d")"; done
    if [ -w "$d" ]; then "$@"; else sudo "$@"; fi
}

if [ "$1" = "--uninstall" ]; then
    rm -f "$APPS/$APP_ID.desktop" "$ICONS/scalable/apps/$APP_ID.svg"
    [ -e "$BIN/scriptures" ] && as_root rm -f "$BIN/scriptures"
    echo "Removed Scriptures from the app list and $BIN (the library in $DATA is kept)."
    exit 0
fi

make -C "$ROOT" >/dev/null
as_root install -D -m 755 "$ROOT/build/scriptures" "$BIN/scriptures"

# Run from $BIN, the program finds no data/ beside it and uses $DATA instead:
# carry over the library built up in the source tree the first time.
if [ ! -e "$DATA" ] && [ -d "$ROOT/data" ]; then
    mkdir -p "$(dirname "$DATA")"
    cp -a "$ROOT/data" "$DATA"
    echo "Copied the library from $ROOT/data to $DATA."
fi

mkdir -p "$APPS" "$ICONS/scalable/apps"
cp "$ROOT/packaging/$APP_ID.svg" "$ICONS/scalable/apps/$APP_ID.svg"
cat > "$APPS/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Scriptures
GenericName=Scripture Reader
Comment=Read the scriptures offline with footnotes, search, highlights and notes
Exec=$BIN/scriptures
Icon=$APP_ID
Terminal=false
Categories=Education;Literature;
Keywords=scripture;bible;book of mormon;gospel library;church;
StartupNotify=true
StartupWMClass=scriptures
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true
echo "Installed: $BIN/scriptures, and \"Scriptures\" is in the app list."
