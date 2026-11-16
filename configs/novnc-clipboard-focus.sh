#!/bin/sh
set -eu

# The pinned noVNC client reads the host clipboard only when its canvas gains
# DOM focus. Returning from another tab/app can leave that canvas focused, so
# refresh on window activation and canvas clicks as well. Permission checks and
# concurrent-read protection remain in the existing handleClipboardFocus hook.
clipboard_source=/opt/noVNC/core/rfb.js
clipboard_marker='// CBC clipboard focus sync'
if grep -Fq "$clipboard_marker" "$clipboard_source"; then
    exit 0
fi

clipboard_add='        this._canvas.addEventListener("focus", this._eventHandlers.handleClipboardFocus);'
clipboard_remove='        this._canvas.removeEventListener("focus", this._eventHandlers.handleClipboardFocus);'
if [ "$(grep -Fc "$clipboard_add" "$clipboard_source")" != 1 ] ||
   [ "$(grep -Fc "$clipboard_remove" "$clipboard_source")" != 1 ]; then
    echo 'Unsupported noVNC clipboard hooks; review the pinned client before updating.' >&2
    exit 1
fi

clipboard_tmp=$(mktemp "${clipboard_source}.cbc-clipboard.XXXXXX")
trap 'rm -f "$clipboard_tmp"' EXIT
awk -v add="$clipboard_add" -v remove="$clipboard_remove" '
    {
        print
        if ($0 == add) {
            print "        // CBC clipboard focus sync"
            print "        window.addEventListener(\"focus\", this._eventHandlers.handleClipboardFocus);"
            print "        this._canvas.addEventListener(\"mousedown\", this._eventHandlers.handleClipboardFocus);"
        } else if ($0 == remove) {
            print "        window.removeEventListener(\"focus\", this._eventHandlers.handleClipboardFocus);"
            print "        this._canvas.removeEventListener(\"mousedown\", this._eventHandlers.handleClipboardFocus);"
        }
    }
' "$clipboard_source" > "$clipboard_tmp"
chmod 644 "$clipboard_tmp"
mv -f "$clipboard_tmp" "$clipboard_source"
