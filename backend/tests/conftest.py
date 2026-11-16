import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import monitor


@pytest.fixture(autouse=True)
def no_background_monitor(monkeypatch):
    """单元测试显式调用 tick，不启动会访问 Docker 的后台线程。"""
    monkeypatch.setattr(monitor, 'start', lambda: None)
    monkeypatch.setattr(monitor, 'stop', lambda: None)
