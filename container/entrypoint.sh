#!/bin/sh
set -eu
umask 077

# Policies are set before opening any listener or starting Chromium. The browser
# shares only the guard's network namespace, never its capabilities or filesystem.
for family in iptables ip6tables; do
    "$family" -P INPUT DROP
    "$family" -P OUTPUT DROP
    "$family" -P FORWARD DROP
    "$family" -F
done
# Docker's embedded resolver uses DNAT before filter/OUTPUT. Drop it in raw/OUTPUT
# too, so allowing loopback TCP cannot accidentally permit external DNS queries.
iptables -t raw -F OUTPUT
iptables -t raw -A OUTPUT -d 127.0.0.11 -j DROP
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -i lo -p tcp -j ACCEPT
iptables -A OUTPUT -o lo -p tcp -j ACCEPT

case "${ROLE:?ROLE required}" in
    guard)
        iptables -A OUTPUT -d "${BRIDGE_IP:?}" -p tcp --dport 8080 -j ACCEPT
        iptables -A INPUT -p tcp --dport 5800 -j ACCEPT
        # The egress interface has no permitted new connection except its GOST;
        # forwarding, UDP, external DNS and IPv6 stay blocked.
        touch /run/firewall-ready
        exec sleep infinity
        ;;
    proxy)
        iptables -A INPUT -s "${BROWSER_IP:?}" -d "${BRIDGE_IP:?}" -p tcp --dport 8080 -j ACCEPT
        # An empty upstream allowlist deliberately creates an offline instance.
        python3 - <<'PY'
import ipaddress,json,subprocess
c=json.load(open('/settings/firewall.json'))
for value in c['upstream_ipv4']:
    address=str(ipaddress.IPv4Address(value))
    subprocess.run(['iptables','-A','OUTPUT','-d',address,'-p','tcp','--dport',str(int(c['port'])),'-j','ACCEPT'],check=True)
PY
        cp /settings/gost.json /run/gost.json
        chmod 600 /run/gost.json
        chown 65532:65532 /run/gost.json
        mihomo_pid=''
        if [ -f /settings/mihomo.json ]; then
            cp /settings/mihomo.json /run/mihomo.json
            chmod 600 /run/mihomo.json
            chown 65532:65532 /run/mihomo.json
            mkdir -p /tmp/mihomo
            chown 65532:65532 /tmp/mihomo
            # One selected node, global mode. It may only leave through the firewall allowlist.
            setpriv --reuid=65532 --regid=65532 --clear-groups --bounding-set=-all --no-new-privs \
                mihomo -d /tmp/mihomo -f /run/mihomo.json >/tmp/mihomo.log 2>&1 &
            mihomo_pid=$!
            echo "$mihomo_pid" >/run/mihomo.pid
            if ! python3 -c 'import socket,time
deadline=time.time()+20
while time.time()<deadline:
    try:
        s=socket.create_connection(("127.0.0.1",1080),1); s.close(); raise SystemExit(0)
    except OSError:
        time.sleep(0.4)
raise SystemExit(1)'; then
                echo '所选节点的本地代理没有就绪。' >&2
                tail -n 30 /tmp/mihomo.log >&2 || true
                exit 1
            fi
        fi
        touch /run/firewall-ready
        # GOST cannot modify the firewall after initialization.
        # kill -0 fails across the uid drop when the supervisor has no CAP_KILL,
        # so liveness is /proc, not a signal.
        setpriv --reuid=65532 --regid=65532 --clear-groups --bounding-set=-all --no-new-privs gost -C /run/gost.json >/tmp/gost.log 2>&1 &
        gost_pid=$!
        alive() { [ -d "/proc/$1" ]; }
        stop_children() {
            kill -TERM "$gost_pid" ${mihomo_pid:+$mihomo_pid} 2>/dev/null || true
            sleep 1
            kill -KILL "$gost_pid" ${mihomo_pid:+$mihomo_pid} 2>/dev/null || true
        }
        trap 'stop_children; exit 0' TERM INT
        sleep 1
        if ! alive "$gost_pid"; then
            echo '本地转发没有就绪。' >&2
            cat /tmp/gost.log >&2 || true
            stop_children
            exit 1
        fi
        while alive "$gost_pid" && { [ -z "$mihomo_pid" ] || alive "$mihomo_pid"; }; do
            sleep 1
        done
        echo '代理进程已退出。' >&2
        cat /tmp/gost.log >&2 || true
        tail -n 30 /tmp/mihomo.log >&2 || true
        stop_children
        exit 1
        ;;
    *) echo 'Invalid container role' >&2; exit 2 ;;
esac
