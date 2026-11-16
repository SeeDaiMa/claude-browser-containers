#!/bin/bash
# 准备管理台依赖和前端。不创建实例，不改 Clash、系统代理或防火墙。
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$root"
if ! command -v python3 >/dev/null || ! command -v node >/dev/null || ! command -v pnpm >/dev/null || ! command -v uv >/dev/null; then
    echo '需要本机已有 Python 3.12、Node、pnpm 和 uv。不会自动安装系统软件。' >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo 'Docker 不可用。请先启动 Docker Desktop。' >&2
    exit 1
fi
uv sync --project "$root/backend"
pnpm install --dir "$root/frontend" --frozen-lockfile
pnpm --dir "$root/frontend" run build
if ! docker image inspect cbc-network:1 >/dev/null 2>&1 || ! docker image inspect cbc-chromium:1 >/dev/null 2>&1; then
    echo '浏览器镜像还没有构建。确认没有其他人正在使用实例后，可以运行 ./scripts/init.sh。' >&2
fi
echo '管理台依赖已准备。启动：./scripts/start-manager.sh'
