ARG GOST_IMAGE=gogost/gost:3.3.3@sha256:f266f957c6e4f8d433ed23202a5745fa4969d08f3f0ef034fb9357345e16ad67
FROM ${GOST_IMAGE}
USER root
RUN apk add --no-cache iptables iproute2-minimal curl python3 setpriv tcpdump ca-certificates
COPY entrypoint.sh /usr/local/bin/cbc-entrypoint
COPY probe.py /opt/cbc/probe.py
# mihomo v1.19.34 linux/arm64, gz sha256 daaf78a6240a282cdfd75bcbf98fe321f6bd52765cc29e79f344191198f0b50f
COPY mihomo /usr/local/bin/mihomo
RUN chmod 755 /usr/local/bin/cbc-entrypoint /usr/local/bin/mihomo
ENTRYPOINT ["/usr/local/bin/cbc-entrypoint"]
