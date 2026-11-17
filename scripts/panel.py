#!/usr/bin/env python3
"""兼容入口：启动本机实例管理台。"""

import os

import cbc


def serve():
    backend = cbc.ROOT / 'backend'
    python = backend / '.venv' / 'bin' / 'python'
    if not python.exists():
        raise RuntimeError('还没有安装管理台依赖。请先运行 ./scripts/setup.sh。')
    os.chdir(backend)
    os.execv(
        str(python),
        [
            str(python),
            '-m',
            'uvicorn',
            'app.main:app',
            '--host',
            '127.0.0.1',
            '--port',
            '5790',
            '--log-level',
            'warning',
        ],
    )
