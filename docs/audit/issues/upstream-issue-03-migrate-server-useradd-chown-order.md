> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title` 后的内容）。

---

**title:** `[migrate] migrate-server.sh 创建 gipfel 用户缺少 -U，且 push 模式 chown 排在建用户之前并被吞错`

## 环境

| 项 | 值 |
| --- | --- |
| OS | Debian GNU/Linux 13 (trixie)，`passwd 1:4.17.4-2` |
| 分支 / commit | `bugfix-merged` / `371cf57e1dc6248806d00094645a606e85fbe7b8` |
| 受影响文件 | `scripts/migrate-server.sh`（sha256 `9b1f4e9c2bdb2f64f2a057a923f7585db2051b3574f7c18efb4ce6816179c5ba`）、`docs/MIGRATION.md:242-243` |
| 相关单元 | `deploy/gipfel.service:9-10`、`deploy/logviewer.service:9-10`（`User=gipfel` / `Group=gipfel`） |

## 问题描述

`migrate-server.sh` 建用户时**没有 `-U`**：

```bash
# push 模式 :338-340
if ! id gipfel >/dev/null 2>&1; then
    sudo useradd -r -s /usr/sbin/nologin gipfel || true
fi

# pull 模式 :500-502
if ! id gipfel >/dev/null 2>&1; then
    sudo useradd -r -s /usr/sbin/nologin gipfel || true
fi
```

是否同时创建同名组，取决于目标机 `/etc/login.defs` 的 `USERGROUPS_ENAB`：

- `USERGROUPS_ENAB yes`（Debian/Ubuntu 默认）→ 组会被创建，暂时看不出问题；
- `USERGROUPS_ENAB no`（部分加固镜像 / 定制发行版 / `useradd -N` 场景）→ **只建用户、不建组**，随后：
  - `migrate-server.sh:505`（pull 模式）`sudo chown -R gipfel:gipfel "$INSTALL_DIR/backend"` —— **没有 `|| true`**，在 `set -euo pipefail`（脚本第 20 行）下直接中止迁移；
  - 即使绕过，`deploy/*.service` 的 `Group=gipfel` 会让服务以 **216/GROUP** 启动失败；
  - `migrate-server.sh:329`（push 模式）`sudo chown -R gipfel:gipfel … 2>/dev/null || true` —— 吞掉错误，且它排在 `:338` **建用户之前**，结果是文件留在 `root:root`：服务能起来，但以 `gipfel` 身份写 `db.sqlite3` / `uploads` 时权限拒绝（无报错的隐性故障）。

同样的写法还出现在 `docs/MIGRATION.md:242-243`（手工迁移步骤），其 `chown` 也没有容错。

## 复现步骤

```bash
# 1) 无 gipfel 用户、无组；模拟 USERGROUPS_ENAB=no
useradd -r -K USERGROUPS_ENAB=no -s /usr/sbin/nologin gipfel
echo "rc=$?"                      # 0
id gipfel                          # uid=999(gipfel) gid=100(users) …  ← 主组是 users
getent group gipfel                # （空）组不存在

# 2) migrate-server.sh:505 原样
sudo chown -R gipfel:gipfel /opt/gipfel/backend
```

实际输出：

```
chown: invalid group: 'gipfel:gipfel'
```

组不存在时 systemd 的表现（实测，真 systemd）：

```ini
[Service]
User=gipfel
Group=gipfel
ExecStart=/bin/true
```

```
Active: failed (Result: exit-code)
Process: 548 ExecStart=/bin/true (code=exited, status=216/GROUP)
gipfel-repro2.service: Failed to determine credentials for group 'gipfel': Unknown group
```

## 期望结果

- 创建运行用户时显式 `-U`（或显式 `groupadd`），不依赖 `USERGROUPS_ENAB`；
- 建用户失败时不要吞错；
- `chown` 必须在建用户/组**之后**执行，并至少校验 `id gipfel` / `getent group gipfel`；
- `docs/MIGRATION.md` 的手工步骤同步修正。

## 建议修复

```bash
# scripts/migrate-server.sh：两处 useradd 补 -U（或统一调用 scripts/lib/deploy-common.sh 的 ensure_runtime_user）
- sudo useradd -r -s /usr/sbin/nologin gipfel || true
+ sudo useradd -r -s /usr/sbin/nologin -U gipfel || true
```

```bash
# push 模式：把 :329 的 chown 移到 :338 建用户之后，并去掉静默吞错
- remote_exec "$REMOTE" "sudo chown -R gipfel:gipfel '$INSTALL_DIR/backend' 2>/dev/null || true"
+# （移动到建用户之后）
+ remote_exec "$REMOTE" "sudo chown -R gipfel:gipfel '$INSTALL_DIR/backend'"
```

```bash
# pull 模式 :505 之前加断言，避免用 chown 的报错暴露建用户失败
sudo id gipfel >/dev/null && sudo getent group gipfel >/dev/null \
    || { echo "运行用户/组 gipfel 不存在，无法切换归属"; exit 1; }
sudo chown -R gipfel:gipfel "$INSTALL_DIR/backend"
```

```markdown
# docs/MIGRATION.md:242
- sudo useradd -r -s /usr/sbin/nologin gipfel 2>/dev/null || true
+ sudo useradd -r -s /usr/sbin/nologin -U gipfel
```

## 影响

- 迁移场景下触发，属于"数据已 rsync 到新机、服务却起不来"的故障，排障成本高（报错出现在 chown 或服务启动日志，而非迁移步骤）；
- push 模式的 `root:root` 属主是**静默**故障：服务 active 但写入失败，只有业务操作时才暴露。

## 验证方式

```bash
bash code_audit/_repro_gipfel_user_group.sh   # 期望 PASS=16 FAIL=0
```

相关断言（Debian 13 实测）：

| 场景 | 断言 | 实测 |
| --- | --- | --- |
| E 不带 `-U` + `USERGROUPS_ENAB=no` | useradd rc / 组是否创建 / `chown gipfel:gipfel` rc | `0` / 未创建 / `1`（`invalid group`） |
| D2 unit `Group=gipfel` 而组不存在 | 退出状态 | `216/GROUP` |
