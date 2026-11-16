#!/bin/sh
set -eu

# Keep the host's normal arrow over the remote desktop. The server still sends
# cursor updates, but noVNC clears its bitmap instead of displaying remote hover
# shapes. This also clears the software cursor used by touch/fallback clients.
cursor_source=/opt/noVNC/core/rfb.js
cursor_marker='// CBC native arrow stays unchanged on hover.'
if grep -Fq "$cursor_marker" "$cursor_source"; then
    exit 0
fi

for cursor_anchor in \
    '        const image = this._shouldShowDotCursor() ? RFB.cursors.dot : this._cursorImage;' \
    '        this._cursor.change(image.rgbaPixels,' \
    '                            image.hotx, image.hoty,' \
    '                            image.w, image.h'; do
    if [ "$(grep -Fc "$cursor_anchor" "$cursor_source")" != 1 ]; then
        echo 'Unsupported noVNC cursor hooks; review the pinned client before updating.' >&2
        exit 1
    fi
done

cursor_tmp=$(mktemp "${cursor_source}.cbc-cursor.XXXXXX")
trap 'rm -f "$cursor_tmp"' EXIT
awk '
    skip {
        skip--
        next
    }
    $0 == "        const image = this._shouldShowDotCursor() ? RFB.cursors.dot : this._cursorImage;" {
        print "        // CBC native arrow stays unchanged on hover."
        print "        this._cursor.clear();"
        print "        this._canvas.style.cursor = \047default\047;"
        skip = 4
        next
    }
    { print }
    END { if (skip) exit 1 }
' "$cursor_source" > "$cursor_tmp"
chmod 644 "$cursor_tmp"
mv -f "$cursor_tmp" "$cursor_source"
