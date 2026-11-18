import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import catalog
import cbc
import monitor


@pytest.fixture(autouse=True)
def no_background_monitor(monkeypatch):
    """单元测试显式调用 tick，不启动会访问 Docker 的后台线程。"""
    monkeypatch.setattr(monitor, 'start', lambda: None)
    monkeypatch.setattr(monitor, 'stop', lambda: None)


@pytest.fixture(autouse=True)
def no_network_retry_wait(monkeypatch):
    """重试次数照常生效，只去掉等待，避免模拟失败的测试平白多等几秒。"""
    monkeypatch.setattr(cbc, 'EXIT_PROBE_RETRY_DELAY', 0)
    monkeypatch.setattr(catalog, 'DOH_RETRY_DELAY', 0)
