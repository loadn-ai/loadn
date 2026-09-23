"""loadn_webui——多 agent WebUI 任务平台（无头 Claude Code 会话驱动）。

每个对话 = 一个任务 = 一个独立工作区；底层 `claude -p --output-format
stream-json` 逐行消费，SSE 实时推送给浏览器；聊天即时 + 长任务后台统一为
同一套 turn 队列。执行引擎/判死/进程治理承袭 papergo（同源 kaggo）。
"""
