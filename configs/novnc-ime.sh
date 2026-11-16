#!/bin/sh
set -eu

# Install the desktop text input beside the pinned Keyboard implementation.
# Existing clipboard, Command/Ctrl mapping and touch UI keep their own hooks.
ime_source=/opt/noVNC/core/rfb.js
ime_marker='// CBC desktop IME bridge v1.'
if grep -Fq "$ime_marker" "$ime_source"; then
    exit 0
fi

# A browser may have been created while this fix was being developed. Normalize
# only our known earlier hooks, then validate and apply the complete bridge.
ime_base=$(mktemp "${ime_source}.cbc-ime-base.XXXXXX")
ime_tmp=$(mktemp "${ime_source}.cbc-ime.XXXXXX")
trap 'rm -f "$ime_base" "$ime_tmp"' EXIT
awk '
    $0 == "import Keyboard from \"./input/cbc-ime.js\";" {
        print "import Keyboard from \"./input/keyboard.js\";"; next
    }
    $0 == "        this._keyboard = new Keyboard(this._canvas, this);" {
        print "        this._keyboard = new Keyboard(this._canvas);"; next
    }
    $0 == "        this._keyboard._target.focus(options);" {
        print "        this._canvas.focus(options);"; next
    }
    $0 == "        this._keyboard._target.blur();" {
        print "        this._canvas.blur();"; next
    }
    /this\._keyboard\.(deferClipboard|remoteClipboard|clipboardProvided)/ { next }
    /this\._keyboard\._target\.(add|remove)EventListener\("focus", this\._eventHandlers\.handleClipboardFocus\);/ {
        sub(/this\._keyboard\._target/, "this._canvas")
    }
    { print }
' "$ime_source" > "$ime_base"

for ime_anchor in \
    'import Keyboard from "./input/keyboard.js";' \
    '        this._keyboard = new Keyboard(this._canvas);' \
    '                        RFB.messages.extendedClipboardProvide(this._sock, [extendedClipboardFormatText], [this._clipboardText]);' \
    '        this._canvas.focus(options);' \
    '        this._canvas.blur();' \
    '    clipboardPasteFrom(text) {' \
    '    async _writeClipboard(text) {' \
    '        this._canvas.addEventListener("focus", this._eventHandlers.handleClipboardFocus);' \
    '        this._canvas.removeEventListener("focus", this._eventHandlers.handleClipboardFocus);'; do
    if [ "$(grep -Fc "$ime_anchor" "$ime_base")" != 1 ]; then
        echo 'Unsupported noVNC IME hooks; review the pinned client before updating.' >&2
        exit 1
    fi
done

awk '
    $0 == "import Keyboard from \"./input/keyboard.js\";" {
        print "import Keyboard from \"./input/cbc-ime.js\";"
        print "// CBC desktop IME bridge v1."
        next
    }
    $0 == "        this._canvas.focus(options);" {
        print "        this._keyboard._target.focus(options);"
        next
    }
    $0 == "        this._keyboard = new Keyboard(this._canvas);" {
        print "        this._keyboard = new Keyboard(this._canvas, this);"
        next
    }
    $0 == "    clipboardPasteFrom(text) {" {
        print
        print "        if (this._keyboard.deferClipboard && this._keyboard.deferClipboard(text)) return;"
        next
    }
    $0 == "    async _writeClipboard(text) {" {
        print
        print "        if (this._keyboard.remoteClipboard && this._keyboard.remoteClipboard(text)) return;"
        next
    }
    $0 == "                        RFB.messages.extendedClipboardProvide(this._sock, [extendedClipboardFormatText], [this._clipboardText]);" {
        print
        print "                        if (this._keyboard.clipboardProvided) this._keyboard.clipboardProvided();"
        next
    }
    $0 == "        this._canvas.blur();" {
        print "        this._keyboard._target.blur();"
        next
    }
    /this\._canvas\.(add|remove)EventListener\("focus", this\._eventHandlers\.handleClipboardFocus\);/ {
        sub(/this\._canvas/, "this._keyboard._target")
    }
    { print }
' "$ime_base" > "$ime_tmp"
chmod 644 "$ime_tmp"
mv -f "$ime_tmp" "$ime_source"
