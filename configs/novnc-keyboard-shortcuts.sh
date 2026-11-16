#!/bin/sh
set -eu

# The pinned client special-cases only Cmd+C/V/X and otherwise sends Command as
# Alt/Super. Use the regular modifier tracking for both Command keys so editing
# shortcuts, Shift combinations and repeated shortcuts behave like remote Ctrl.
shortcut_source=/opt/noVNC/core/input/keyboard.js
shortcut_marker='// CBC Mac Command acts as remote Control.'
if grep -Fq "$shortcut_marker" "$shortcut_source"; then
    exit 0
fi

for shortcut_anchor in \
    "            const codes = [ 'KeyC', 'KeyV', 'KeyX' ];" \
    '                    keysym = KeyTable.XK_Alt_L;' \
    '                    keysym = KeyTable.XK_Super_L;' \
    '        this.onkeyevent(keysym, code, down, numlock, capslock);' \
    '    _interruptMetaSequence() {' \
    "            (e.metaKey && code !== 'MetaLeft' && code !== 'MetaRight')) {"; do
    if [ "$(grep -Fc "$shortcut_anchor" "$shortcut_source")" != 1 ]; then
        echo 'Unsupported noVNC keyboard hooks; review the pinned client before updating.' >&2
        exit 1
    fi
done

shortcut_tmp=$(mktemp "${shortcut_source}.cbc-shortcuts.XXXXXX")
trap 'rm -f "$shortcut_tmp"' EXIT
awk '
    skip_method {
        if ($0 == "    }") skip_method = 0
        next
    }
    /this\._metaArmed = false;.*Mac copy\/paste/ { next }
    /^        if \(this\._metaArmed\) \{/ { skip_shortcuts = 1; next }
    skip_shortcuts {
        if ($0 ~ /^        \/\/ We cannot handle keys we cannot track/) {
            skip_shortcuts = 0
        } else {
            next
        }
    }
    /^        \/\/ Possible start of copy\/paste sequence/ { skip_arm = 1; next }
    skip_arm {
        if ($0 == "        }") skip_arm = 0
        next
    }
    /^    _interruptMetaSequence\(\) \{/ { skip_method = 1; next }
    /\/\/ We can.t get a release in the middle of a meta sequence/ { skip_comment = 1; next }
    skip_comment { skip_comment = 0; next }
    /\/\/ Prevent meta key being processed after losing focus/ { next }
    /this\._interruptMetaSequence\(\);/ { next }
    $0 == "        this.onkeyevent(keysym, code, down, numlock, capslock);" {
        print "        // Keep extended VNC scancodes consistent with the mapped modifier."
        print "        if (browser.isMac() || browser.isIOS()) {"
        print "            if (code === \047MetaLeft\047) code = \047ControlLeft\047;"
        print "            else if (code === \047MetaRight\047) code = \047ControlRight\047;"
        print "        }"
    }
    $0 == "                case KeyTable.XK_Super_L:" {
        print "                // CBC Mac Command acts as remote Control."
    }
    $0 == "                    keysym = KeyTable.XK_Alt_L;" {
        print "                    keysym = KeyTable.XK_Control_L;"
        next
    }
    $0 == "                    keysym = KeyTable.XK_Super_L;" {
        print "                    keysym = KeyTable.XK_Control_R;"
        next
    }
    /\(e.metaKey && code !==/ {
        print "            (e.metaKey && !["
        print "                \047MetaLeft\047, \047MetaRight\047, \047ShiftLeft\047, \047ShiftRight\047,"
        print "                \047ControlLeft\047, \047ControlRight\047, \047AltLeft\047, \047AltRight\047"
        print "            ].includes(code))) {"
        next
    }
    { print }
    END {
        if (skip_shortcuts || skip_arm || skip_method || skip_comment) exit 1
    }
' "$shortcut_source" > "$shortcut_tmp"
if grep -Eq '_metaArmed|_metaSendKeyEvent|_interruptMetaSequence' "$shortcut_tmp"; then
    echo 'Incomplete noVNC keyboard patch; original client left unchanged.' >&2
    exit 1
fi
chmod 644 "$shortcut_tmp"
mv -f "$shortcut_tmp" "$shortcut_source"
