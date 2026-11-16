#!/bin/sh
set -eu

# Publish the screenshot paste endpoint on the existing noVNC port.
# Authentication, when enabled, still applies through the server auth_request.
# cinit only starts services reachable from the default group, so the helper is
# attached there and waits for Xvnc.
site=/opt/base/etc/nginx/default_site.conf
marker='# CBC screenshot clipboard'
config=/etc/cbc-clipboard-image.nginx.conf
anchor='	include /opt/base/etc/nginx/include/vnc.conf;'
service_dir=/etc/services.d/cbc-clipboard-image

if [ ! -f "$config" ]; then
    echo 'Screenshot clipboard nginx config is not mounted.' >&2
    exit 1
fi
if [ ! -x "$service_dir/run" ]; then
    echo 'Screenshot clipboard service is not executable.' >&2
    exit 1
fi
touch "$service_dir/xvnc.dep"
touch /etc/services.d/gui/cbc-clipboard-image.dep

# An image-only host clipboard reads back as empty text. Do not push that
# empty value over the remote text clipboard.
rfb=/opt/noVNC/core/rfb.js
sync_marker='CBC skip empty sync while the host clipboard is an image'
sync_anchor='            const text = await navigator.clipboard.readText();'
sync_follow='            if (this._clipboardAutoSyncEnabled) {'
if ! grep -Fq "$sync_marker" "$rfb"; then
    if [ "$(grep -Fc "$sync_anchor" "$rfb")" != 1 ] || [ "$(grep -Fc "$sync_follow" "$rfb")" != 1 ]; then
        echo 'Unsupported noVNC clipboard read; review the pinned client before updating.' >&2
        exit 1
    fi
    sync_tmp=$(mktemp "${rfb}.cbc-clipboard.XXXXXX")
    awk -v anchor="$sync_anchor" -v follow="$sync_follow" '
        $0 == anchor { hold = 1; print; next }
        hold && $0 == follow {
            print "            // CBC skip empty sync while the host clipboard is an image"
            print "            if (!(this._keyboard.skipHostClipboardSync && await this._keyboard.skipHostClipboardSync(text))"
            print "                && this._clipboardAutoSyncEnabled) {"
            hold = 0
            next
        }
        { print }
    ' "$rfb" > "$sync_tmp"
    if ! grep -Fq "$sync_marker" "$sync_tmp"; then
        rm -f "$sync_tmp"
        echo 'Screenshot clipboard sync hook was not applied.' >&2
        exit 1
    fi
    chmod 644 "$sync_tmp"
    mv -f "$sync_tmp" "$rfb"
fi

nginx_test() {
    pidfile=/run/nginx/nginx.pid
    backup=
    if [ -f "$pidfile" ]; then
        backup=$(mktemp)
        cp -a "$pidfile" "$backup"
    fi
    if ! /opt/base/sbin/nginx -c /opt/base/etc/nginx/nginx.conf -t; then
        if [ -n "$backup" ]; then
            cp -a "$backup" "$pidfile"
            rm -f "$backup"
        fi
        return 1
    fi
    if [ -n "$backup" ]; then
        cp -a "$backup" "$pidfile"
        rm -f "$backup"
    else
        rm -f /run/nginx/nginx.pid /var/run/nginx/nginx.pid
        if [ -n "${USER_ID:-}" ] && [ -d /run/nginx ]; then
            chown "${USER_ID}:${GROUP_ID}" /run/nginx /var/run/nginx 2>/dev/null || true
        fi
    fi
}

patch_site() {
    tmp=$(mktemp "${site}.cbc-clipboard.XXXXXX")
    backup=$(mktemp "${site}.cbc-clipboard-backup.XXXXXX")
    cp "$site" "$backup"
    awk -v anchor="$anchor" '
        $0 == anchor {
            print
            print "	# CBC screenshot clipboard"
            print "	include /etc/cbc-clipboard-image.nginx.conf;"
            next
        }
        { print }
    ' "$site" > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$site"
    if ! nginx_test; then
        mv -f "$backup" "$site"
        rm -f "$tmp"
        echo 'Screenshot clipboard nginx config failed validation.' >&2
        return 1
    fi
    rm -f "$backup"
}

if ! grep -Fq "$marker" "$site"; then
    if [ "$(grep -Fc "$anchor" "$site")" != 1 ]; then
        echo 'Unsupported noVNC nginx config; review the pinned client before updating.' >&2
        exit 1
    fi
    patch_site
else
    nginx_test
fi
if [ -s /run/nginx/nginx.pid ] && kill -0 "$(cat /run/nginx/nginx.pid)" 2>/dev/null; then
    /opt/base/sbin/nginx -c /opt/base/etc/nginx/nginx.conf -s reload
fi
