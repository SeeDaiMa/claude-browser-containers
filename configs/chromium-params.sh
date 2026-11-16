#!/bin/sh
set -eu
# The upstream SUID probe reports failure with no-new-privileges. Test the
# unprivileged user namespace directly and never fall back to --no-sandbox.
if ! unshare -U /bin/true; then
    echo 'Chromium user namespace sandbox unavailable; refusing to start.' >&2
    exit 1
fi
printf '%s\n' '--disable-setuid-sandbox' '--no-first-run' '--no-default-browser-check' '--start-maximized' '--user-data-dir=/config/chromium'
# Match noVNC's 2x framebuffer so Retina text stays sharp at the same CSS size.
printf '%s\n' '--force-device-scale-factor=2'
printf '%s\n' "--proxy-server=http://${CBC_BRIDGE_IP}:8080" '--proxy-bypass-list=<-loopback>' "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE ${CBC_BRIDGE_IP}, EXCLUDE localhost" '--disable-quic' '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'
if [ "${CHROMIUM_REMOTE_DEBUGGING:-0}" = 1 ]; then
    printf '%s\n' '--remote-debugging-port=9223'
fi
printf '%s\n' 'https://claude.ai'
