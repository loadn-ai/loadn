# loadn 发布-升级-回滚手册（R7）

## 架构

```
/opt/loadn/                          # 部署根（与代码仓彻底分离）
├── releases/v1.0.0/                 # 每版自包含（代码+venv+前端）
├── current -> releases/v1.0.1      # 原子符号链接
├── wheelhouse/<hash>/              # 离线 wheel 缓存
├── state/deploy.json               # current/previous/操作历史
└── logs/ops.log                    # 操作审计

/data/code/loadn                    # 代码仓（开发/构建，不直接跑生产）
/data/code/workdaddy                # 数据根（LOADN_WEBUI_HOME，永不搬家）
```

## 日常操作

### 发布新版本（代码仓内）

```bash
cd /data/code/loadn

# 1. 全量测试
.venv/bin/python -m pytest tests/ -q

# 2. 提交 + 打 tag
git add -A && git commit -m "..."
git tag v1.0.2

# 3. 构建 release（前端+venv+冒烟，零停机）
.venv/bin/python -m loadn_webui release build --tag v1.0.2

# 4. 升级生产
loadn-web upgrade v1.0.2
# 或缺省=最新：loadn-web upgrade
```

### 查看状态

```bash
loadn-web release versions    # 列全部 release
loadn-web release status      # 当前版本/磁盘/引用状态
```

### 回滚

```bash
loadn-web rollback            # 回到 previous
loadn-web rollback v1.0.0    # 回到指定版本
```

### 清理旧版本

```bash
loadn-web release gc --keep 4   # 保留 4 个（保护 current/previous/被引用）
```

## 升级流水线（全自动）

```
build（代码仓内，零停机）:
  git archive tag → 前端 npm build → wheelhouse 预热 → venv 离线安装
  → RELEASE.json → 冒烟测试（临时 HOME，不碰生产数据根）

upgrade:
  preflight（release 完整性+数据根可读写+磁盘>2G）
  → DB 备份（backup API，保留 3 份）
  → 等 idle（轮询 turns 状态，超时 --yes 强切）
  → 原子换 current 指针（ln -s + mv -T rename(2)）
  → systemctl restart loadn
  → healthcheck（/api/health 200 + release 版本匹配，超时 90s）
  → 失败自动回滚（翻回 previous + restart + 复验）
```

**停机窗口 = 一次 systemctl restart（秒级）**。turn 子进程靠 KillMode=process 幸存 + 收养续跑。

## 应急恢复（L3——current venv 坏了）

```bash
# 方法 1：loadn-ops wrapper 自动回落 previous
/opt/loadn/bin/loadn-ops rollback

# 方法 2：手动两命令（写死在脑子里的应急卡）
sudo ln -sfn /opt/loadn/releases/v1.0.0 /opt/loadn/current
sudo systemctl restart loadn

# 方法 3：venv 重建（L4——所有 venv 都坏）
loadn-web release repair-venv v1.0.0
```

## L5：极端情况（连 DB 一起退）

```bash
sudo systemctl stop loadn
cp /data/code/workdaddy/var/backups/db-pre-v1.0.2.db \
   /data/code/workdaddy/var/loadn.db
loadn-web rollback v1.0.0
```

仅在出现破坏性 schema 迁移时需要（additive-only 契约下不应发生）。

## 回滚门禁

- `schema_rev`（db.py 常量，RELEASE.json 记录）：目标 < 当前 → 警告但允许
  （additive-only：旧代码可跑新 schema，多余列无害）
- 目标缺列（理论上不应发生）→ 拒绝，需 `--force`... 实际当前实现：缺
  RELEASE.json 的 schema_rev 视为同版

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 2 | preflight/参数失败（未切换） |
| 3 | 已切换但 healthcheck 失败，**已自动回滚** |
| 4 | 回滚也失败（走 L3 应急卡） |
