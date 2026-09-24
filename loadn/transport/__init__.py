"""stdio↔UDS 桥 + daemon 多传输（P2-1，codex stdio-to-uds × app-server 同构）。

引擎进程比 UI 连接活得久：
- `loadn daemon`：UDS 监听（$LOADN_HOME/var/engine.sock）+ 连接 token
  （daemon 启动生成写 0600 token 文件——attach 首行必须带 token）。
- 多连接语义：**同会话单写者**——同一 session 同时只允许一个活跃连接持
  有「写权」（发 turn 请求）；后连者只读（事件流镜像）。UI 断开→引擎收尾
  当前 turn 后挂起等待重连，会话状态不丢。
- 方言不动：桥只换管道不改事件流（v1/v2 协议字节流原样过桥）。
- 心跳沿用 emitter 的 system.heartbeat（30s）——UDS 连接空闲判活同源。
"""
