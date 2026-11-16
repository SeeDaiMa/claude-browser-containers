#!/bin/sh
set -eu

# Chromium renders at 2x. Keep the noVNC controls at their normal size while
# mapping each CSS pixel to two remote pixels, including pointer coordinates.
hidpi_rfb=/opt/noVNC/core/rfb.js
hidpi_ui=/opt/noVNC/app/ui.js
hidpi_marker='// CBC 2x desktop rendering.'
if grep -Fq "$hidpi_marker" "$hidpi_rfb"; then
    exit 0
fi

for hidpi_anchor in \
    '        this._resizeSession = false;' \
    '            this._display.scale = 1.0;' \
    '    _requestRemoteResize() {'; do
    if [ "$(grep -Fc "$hidpi_anchor" "$hidpi_rfb")" != 1 ]; then
        echo 'Unsupported noVNC display hooks; review the pinned client before updating.' >&2
        exit 1
    fi
done
if [ "$(grep -Fc "        UI.initSetting('quality', 6);" "$hidpi_ui")" != 1 ]; then
    echo 'Unsupported noVNC quality setting; original client left unchanged.' >&2
    exit 1
fi

hidpi_rfb_tmp=$(mktemp "${hidpi_rfb}.cbc-hidpi.XXXXXX")
hidpi_ui_tmp=$(mktemp "${hidpi_ui}.cbc-hidpi.XXXXXX")
trap 'rm -f "$hidpi_rfb_tmp" "$hidpi_ui_tmp"' EXIT
awk '
    $0 == "        this._resizeSession = false;" {
        print
        print "        // CBC 2x desktop rendering."
        print "        this._cbcPixelRatio = 2;"
        next
    }
    $0 == "            this._display.scale = 1.0;" {
        print "            this._display.scale = 1 / this._cbcPixelRatio;"
        next
    }
    $0 == "    _requestRemoteResize() {" { remote_resize = 1 }
    remote_resize && $0 == "        const size = this._screenSize();" {
        print
        print "        size.w *= this._cbcPixelRatio;"
        print "        size.h *= this._cbcPixelRatio;"
        resized++
        next
    }
    remote_resize && $0 == "    }" { remote_resize = 0 }
    { print }
    END { if (resized != 1) exit 1 }
' "$hidpi_rfb" > "$hidpi_rfb_tmp"
sed "s/UI.initSetting('quality', 6);/UI.initSetting('quality', 9);/" \
    "$hidpi_ui" > "$hidpi_ui_tmp"
chmod 644 "$hidpi_rfb_tmp" "$hidpi_ui_tmp"
mv -f "$hidpi_rfb_tmp" "$hidpi_rfb"
mv -f "$hidpi_ui_tmp" "$hidpi_ui"
