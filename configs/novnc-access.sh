#!/bin/sh
set -eu

# The published port belongs to the instance, not nginx's internal 5800 port.
port=${CBC_WEB_PORT:-}
case "$port" in
    ''|*[!0-9]*) echo 'CBC_WEB_PORT must be an instance port between 1024 and 65535.' >&2; exit 1 ;;
esac
if [ "${#port}" -gt 5 ] || [ "$port" -lt 1024 ] || [ "$port" -gt 65535 ]; then
    echo 'CBC_WEB_PORT must be an instance port between 1024 and 65535.' >&2
    exit 1
fi

site=/opt/base/etc/nginx/default_site.conf
vnc=/opt/base/etc/nginx/include/vnc.conf
marker="# CBC local access guard v1 port=$port"
if grep -Fq "$marker" "$site" && grep -Fq "$marker" "$vnc"; then
    exit 0
fi
if grep -Fq '# CBC local access guard' "$site" || grep -Fq '# CBC local access guard' "$vnc"; then
    echo 'Existing noVNC access guard differs; recreate the browser container before changing its port.' >&2
    exit 1
fi
if [ "$(grep -Fxc 'server {' "$site")" != 1 ] || [ "$(grep -Fxc 'location ~ /websockify$ {' "$vnc")" != 1 ]; then
    echo 'Unsupported noVNC nginx config; access guard was not applied.' >&2
    exit 1
fi

site_tmp=$(mktemp "${site}.cbc-access.XXXXXX")
vnc_tmp=$(mktemp "${vnc}.cbc-access.XXXXXX")
trap 'rm -f "$site_tmp" "$vnc_tmp"' EXIT
awk -v port="$port" -v marker="$marker" '
    { print }
    $0 == "server {" {
        print "\t" marker
        print "\tif ($http_host !~* \"^(127[.]0[.]0[.]1|localhost):" port "$\") { return 403; }"
    }
' "$site" > "$site_tmp"
awk -v port="$port" -v marker="$marker" '
    { print }
    $0 == "location ~ /websockify$ {" {
        print "\t" marker
        print "\tif ($http_origin !~* \"^http://(127[.]0[.]0[.]1|localhost):(5790|" port ")$\") { return 403; }"
    }
' "$vnc" > "$vnc_tmp"
chmod 644 "$site_tmp" "$vnc_tmp"
mv -f "$site_tmp" "$site"
mv -f "$vnc_tmp" "$vnc"
# The following clipboard initializer validates the full nginx configuration;
# nginx also refuses invalid configuration when its service starts.
