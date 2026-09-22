"""通知通道（P2-2）：三 provider 载荷 / 事件开关 / engine+scheduler 钩子。"""
import asyncio

import pytest

from loadn_webui import notify as notify_mod
from loadn_webui.config import CONFIG


@pytest.fixture()
def calls(monkeypatch):
    got = []

    async def fake_post(url, **kw):
        got.append({"url": url, **kw})

        class R:
            status_code = 200
            text = '{"code":0}'

            def json(self):
                return {"code": 0}
        return R()

    import loadn_webui.resources as res
    monkeypatch.setattr(res, "_post", fake_post)
    return got


async def test_bark_payload_and_events(calls, monkeypatch):
    monkeypatch.setattr(CONFIG.notify, "provider", "bark")
    monkeypatch.setattr(CONFIG.notify, "bark_url", "https://api.day.app/KEY123")
    # on_turn_done 默认关 → 跳过
    assert await notify_mod.send("标题", "正文", event="on_turn_done") is False
    assert not calls
    # on_error 默认开 → 推送
    assert await notify_mod.send("出错了", "详情", event="on_error") is True
    assert calls[0]["url"] == "https://api.day.app/KEY123"
    assert calls[0]["json"]["title"] == "出错了"
    # 无 event 参数 → 无条件推（CLI 测试用）
    assert await notify_mod.send("手动测试") is True


async def test_serverchan_telegram(calls, monkeypatch):
    monkeypatch.setattr(CONFIG.notify, "provider", "serverchan")
    monkeypatch.setattr(CONFIG.notify, "serverchan_key", "SCTKEY")
    assert await notify_mod.send("t", "b") is True
    assert calls[-1]["url"] == "https://sctapi.ftqq.com/SCTKEY.send"
    assert calls[-1]["data"]["title"] == "t"

    monkeypatch.setattr(CONFIG.notify, "provider", "telegram")
    monkeypatch.setattr(CONFIG.notify, "telegram_bot_token", "123:abc")
    monkeypatch.setattr(CONFIG.notify, "telegram_chat_id", "42")
    monkeypatch.setattr(CONFIG.resources, "proxy", "http://192.0.2.137:7890")
    assert await notify_mod.send("t", "b") is True
    assert calls[-1]["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert calls[-1]["proxy"] == "http://192.0.2.137:7890"   # 墙内走 clash


async def test_disabled_provider(calls, monkeypatch):
    monkeypatch.setattr(CONFIG.notify, "provider", "")
    assert await notify_mod.send("x") is False
    assert not calls


async def test_settings_roundtrip(tmp_path, monkeypatch):
    """put_notify → config.yaml 落盘 + 内存 CONFIG 同步。

    隔离方式：给 settings_admin 换独立 Config 实例 + 指向 tmp_path 的
    PATHS（不 reload config 模块——reload 会替换全局 CONFIG/PATHS 对象，
    污染同进程后续所有测试，曾导致 test_share/test_transfer 全挂）。
    """
    from loadn_webui.config import Config
    import loadn_webui.settings_admin as sa
    cfg = Config()
    monkeypatch.setattr(sa, "CONFIG", cfg)
    monkeypatch.setattr(sa, "PATHS", {"root": tmp_path})

    out = sa.put_notify({"provider": "bark", "bark_url": "https://api.day.app/k9",
                         "events": {"on_turn_done": True}})
    assert out["notify"]["provider"] == "bark"
    assert out["notify"]["events"]["on_turn_done"] is True
    assert (tmp_path / "config.yaml").exists()
    assert cfg.notify.provider == "bark"            # 内存 CONFIG 同步（即时生效）
    # 坏 provider 拒绝
    with pytest.raises(ValueError):
        sa.put_notify({"provider": "sms"})


async def test_ping(monkeypatch):
    monkeypatch.setattr(CONFIG.notify, "provider", "")
    assert (await notify_mod.ping())["ok"] is False
    monkeypatch.setattr(CONFIG.notify, "provider", "bark")
    monkeypatch.setattr(CONFIG.notify, "bark_url", "https://api.day.app/x")

    async def fake_send(t, b="", event=""):
        return True
    monkeypatch.setattr(notify_mod, "send", fake_send)
    assert (await notify_mod.ping())["ok"] is True
