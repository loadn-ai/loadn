"""例 9/10：register_provider——自定义 provider 工厂。

注册名 "shout"：env LOADN_PROVIDER=shout 生效。本例复用 fake provider
（零 token），真实场景换成自研协议族适配层。
"""
from __future__ import annotations


def _factory(cfg: dict):
    from loadn.providers.fake import FakeProvider
    return FakeProvider()


def load(ext) -> None:
    ext.register_provider("shout", _factory)
