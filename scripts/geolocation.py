"""只读查询项目内的国家归属库，不向外部服务发送 IP。"""
from functools import lru_cache
import ipaddress
from pathlib import Path

import maxminddb


DATABASE = Path(__file__).resolve().parents[1] / 'backend' / 'resources' / 'Country.mmdb'


@lru_cache(maxsize=1)
def _database():
    try:
        return maxminddb.open_database(DATABASE)
    except (OSError, maxminddb.InvalidDatabaseError):
        return None


@lru_cache(maxsize=1024)
def _country_code(value: str) -> str | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if not address.is_global:
        return None
    database = _database()
    if database is None:
        return None
    try:
        record = database.get(str(address)) or {}
    except (OSError, ValueError, maxminddb.InvalidDatabaseError):
        return None
    code = record.get('country', {}).get('iso_code')
    if not isinstance(code, str) or len(code) != 2 or not code.isascii() or not code.isalpha():
        return None
    return code.upper() if code.upper() not in ('XX', 'ZZ') else None


def country_code(value: str | None) -> str | None:
    # lru_cache 会先对参数求哈希。列表或对象在进入函数体之前就会抛 TypeError。
    if not isinstance(value, str) or not value:
        return None
    return _country_code(value)


country_code.cache_clear = _country_code.cache_clear
