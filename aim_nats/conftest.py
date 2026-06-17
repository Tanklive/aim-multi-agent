"""pytest 配置 — 自动启用 asyncio 支持"""
import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("asyncio") is None:
            item.add_marker(pytest.mark.asyncio)
