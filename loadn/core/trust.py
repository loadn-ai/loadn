"""Workspace 信任门（P0-2，zcode Z1 × pi-P3 同构）——引擎侧再导出壳。

项目级资源（hooks/skills/settings/agents）首次使用须过信任门，未信任
降级跳过。完整语义与 store 实现在顶层 loadn/truststore.py（平台侧
write_settings 物化后 admit 也要写它——进程边界：webui 不入 loadn.core）。

- gate() 从不问人：headless（-p）无处问 = fail-closed（ask→deny 哲学）；
  交互 REPL 启动 preflight 问一次（main.py 在 build_agent 之前调用）
- 消费点：core/hooks.py（项目 settings 的 hooks 段）、core/skills.py
  （项目 skills 发现序）、core/agent_defs.py（项目 agents/*.md）、
  core/permissions.py（项目 permissions 规则）
"""
from __future__ import annotations

from loadn.truststore import (  # noqa: F401
    DIGEST_SCHEMA_VERSION,
    MAX_DIGEST_BYTES,
    MAX_DIGEST_FILES,
    _load_store,
    _resource_files,
    _save_store,
    _store_path,
    admit,
    digest,
    forget,
    gate,
    project_root,
    revoke,
)
