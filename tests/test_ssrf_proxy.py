"""SSRF×代理模式回归：proxied=True 跳过本地 IP 段检查（DNS 污染误拦），
scheme+敏感域后缀仍全查。2026-10-23 研究模式出海实证：
www.google.com 本地解析 2001::1（Teredo 保留段）被误拦致 proxy fetch 全断。"""
import asyncio

import pytest

from loadn_webui.integrations.resources import SsrfBlocked, _ssrf_check


def test_proxied_skips_ip_segment():
    # host 用真会被污染解析的域——即便本地解析出保留段，proxied 也不拦
    asyncio.get_event_loop_policy()
    try:
        asyncio.run(_ssrf_check("https://www.google.com/search?q=x",
                                proxied=True))
    except SsrfBlocked as e:
        pytest.fail(f"proxied 不应因本地解析拦: {e}")


def test_non_proxied_still_blocks_literal_private():
    with pytest.raises(SsrfBlocked, match=r"保留地址"):
        asyncio.run(_ssrf_check("https://127.0.0.1/x"))            # 字面回环
    with pytest.raises(SsrfBlocked, match=r"保留地址"):
        asyncio.run(_ssrf_check("https://192.168.1.1/x"))


def test_scheme_and_domain_block_always():
    with pytest.raises(SsrfBlocked, match=r"协议"):
        asyncio.run(_ssrf_check("ftp://example.com/f"))
    # 敏感域后缀（无论是否 proxied）——取清单首项动态验证
    from loadn_webui.integrations.resources import _ssrf_deny_suffixes
    suf = next(iter(_ssrf_deny_suffixes()))
    with pytest.raises(SsrfBlocked, match=r"敏感域"):
        asyncio.run(_ssrf_check(f"https://a.{suf}/x", proxied=True))
