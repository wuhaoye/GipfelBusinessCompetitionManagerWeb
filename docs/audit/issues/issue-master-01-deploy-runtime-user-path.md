> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title:` 后的内容）。测试分支：`master`（提交 `34013bb`）。

---

**title:** `[deploy] PATH 缺少 /usr/sbin 时创建 gipfel 用户静默失败，脚本以 chown 报错中断`

## 环境

| 项 | 值 |
| --- | --- |
| 分支 / commit | `master` / `34013bb`（`fix(frontend): 放宽输入键端口类型限制`） |
| OS | Debian GNU/Linux 13 (trixie)，内核 6.12.107+deb13-amd64（真机 VMware） |
| 复现环境 | `su`（**不带 `-`**）进入 root 后执行部署；另在 Ubuntu 26.04 上复核 |
| 受影响文件 | `scripts/deploy-linux.sh:221-225`、`scripts/migrate-server.sh:239/250/375/379`、`docs/MIGRATION.md:242` |

## 问题描述

首次部署时脚本跑到前端构建完成之后突然中止，报的是**权限/属主**错误：

```
[FAIL] 脚本在第 <chown 行> 终止（退出码 1）
       命令：chown -R gipfel:gipfel "$INSTALL_DIR"
```

但真正的原因是**创建运行用户 gipfel 失败**，且失败被 `|| true` 吞掉、没有任何输出。

```bash
# scripts/deploy-linux.sh:221-225（master 原文）
if ! id gipfel >/dev/null 2>&1; then
    log "创建专用运行用户 gipfel"
    useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel || true
fi
```

## 复现步骤

```bash
# 1) 进入一个 PATH 不含 /usr/sbin 的 root 环境（su 不带 - 时就是这样）
su                      # 注意：不是 su -
echo "$PATH"            # /usr/local/bin:/usr/bin:/bin:/usr/games
command -v useradd      # 无输出 = 未找到（useradd 在 /usr/sbin）
command -v chown        # /usr/bin/chown  ← 可用
command -v systemctl    # /usr/bin/systemctl ← 可用

# 2) 执行 master 第 221-225 行原文（$INSTALL_DIR 指向任意目录），随后执行脚本同款 chown
INSTALL_DIR=/opt/gipfel
if ! id gipfel >/dev/null 2>&1; then
    log "创建专用运行用户 gipfel"
    useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel || true
fi
chown -R gipfel:gipfel "$INSTALL_DIR"
```

**真机实测输出**（Ubuntu 26.04 / root）：

```
  PATH=/usr/local/bin:/usr/bin:/bin:/usr/games
  useradd -> 未找到
    [INFO] 创建专用运行用户 gipfel
  line 30: useradd: command not found        ← 被 || true 吞掉，脚本继续
    → 建用户失败被 || true 吞掉，脚本继续
  chown: invalid user: 'gipfel:gipfel'
  子 shell 退出码=1
```

## 实际结果

- stdout 里**完全没有**关于 `useradd` 的任何提示；
- 脚本在后续的 `chown -R gipfel:gipfel` 处终止（`set -euo pipefail`）；
- 现场留下"半成品部署"：`.venv`、`db.sqlite3`、`.env`、`staticfiles`、`node_modules`、`frontend-dist` 都已生成（apt/pip/migrate/collectstatic/npm 全部成功），但
  - **没有 `gipfel` 用户和组**；
  - **没有 `/etc/systemd/system/gipfel*.service`**；
  - `.env` 仍是 `664 root:root`；
  - `frontend-dist` 仍是 `root:root`。
- 运维视角看到的是"chown 权限问题"，排查方向被带偏。

**同类第二处**：同一 PATH 下 `nginx -t`（`/usr/sbin/nginx`）会直接 `command not found`，即使修好建用户也会在第 7 步再次失败。

## 期望结果

- 关键系统命令不可用时**立即以明确信息中止**（例如"找不到 `useradd`（PATH=…），请确认已安装 passwd 包，并使用 `su -` 或 sudo 运行"）；
- 脚本自身不应对 `/usr/sbin` 是否在 PATH 中做隐式假设（`apt-get`/`chown`/`systemctl` 都在 `/usr/bin`，所以环境"看起来正常"）；
- 或在 `chown -R gipfel:gipfel` 之前断言用户/组已存在，避免以权限错误的形式暴露建用户失败。

## 根因

1. `useradd` 位于 `/usr/sbin`。`su`（非登录 shell）的 PATH 为 `/usr/local/bin:/usr/bin:/bin:/usr/games`，`useradd` 不存在 → `command not found`（退出码 127）；
2. `|| true` 把 127 吞成成功，脚本继续执行 `migrate` / 前端构建；
3. 后续 `chown -R gipfel:gipfel` 因用户不存在失败（退出码 1），`set -e` 终止脚本。

**附带的同类缺陷（同一次真机复现）**：

| 场景 | 命令 | 实测结果 |
| --- | --- | --- |
| 组已存在、用户不存在 | `useradd -r -s /usr/sbin/nologin -U -d DIR gprobe` | **退出码 9**：`useradd: group gprobe exists - if you want to add this user to that group, use -g.` → 随后 `chown: invalid user: 'gprobe:gprobe'`（守卫只查 `id gipfel`，而 `-U` 要求同名组空闲；`groupadd`/`userdel` 残留、其它包占用同名组都会进入该状态） |
| `migrate-server.sh` 建用户（**无 `-U`**）在 `USERGROUPS_ENAB=no` 的机器上 | `useradd -r -K USERGROUPS_ENAB=no -s /usr/sbin/nologin gprobe` | 退出码 0 但**只建用户不建组** → `chown: invalid group: 'gprobe:gprobe'`；`deploy/*.service` 的 `Group=gipfel` 还会让服务以 `216/GROUP` 启动失败 |
| 对照：PATH 正确时 | 同样的 `useradd -U` 行 | 退出码 0，用户与组一并建出 → **环境本身没问题，问题在 PATH 与被吞掉的错误** |

另：`scripts/migrate-server.sh:239` 的 `chown` 排在 `:250` 建用户**之前**，且带 `2>/dev/null || true`；push 模式下文件会留在 `root:root`，服务能起来但以 `gipfel` 身份写 `db.sqlite3` / `uploads` 时权限拒绝（不报错的隐性故障）。`docs/MIGRATION.md:242` 的手工步骤同样缺 `-U` 且用 `2>/dev/null || true` 掩盖失败。

## 建议修复（供参考，未提交）

```bash
# ① 脚本头部显式补齐 sbin（三个脚本都加）
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

# ② 用「用户与组一起校验、失败即中止」的 helper 取代 221-225
ensure_runtime_user() {
    local user="${1:-gipfel}" dir="${2:-/opt/$user}" group="${3:-$1}"
    id "$user" >/dev/null 2>&1 && getent group "$group" >/dev/null 2>&1 && return 0
    command -v useradd >/dev/null 2>&1 \
        || err "找不到 useradd（PATH=$PATH）：请确认已安装 passwd 包，并用 su - 或 sudo 运行本脚本"
    if getent group "$group" >/dev/null 2>&1; then
        useradd -r -s /usr/sbin/nologin -g "$group" -d "$dir" "$user"   # 组已存在 → 复用
    else
        useradd -r -s /usr/sbin/nologin -U -d "$dir" "$user"            # 一次建出用户与组
    fi
    id "$user" >/dev/null 2>&1 && getent group "$group" >/dev/null 2>&1 \
        || err "创建运行用户/组 $user 失败，请手工创建后重跑"
}
ensure_runtime_user gipfel "$INSTALL_DIR"
```

③ `migrate-server.sh` 两处 `useradd` 补 `-U`（或改调同一 helper），push 模式的 `chown` 移到建用户之后并去掉 `2>/dev/null`；④ `docs/MIGRATION.md:242` 补 `-U`；⑤ 文档明确"必须 `sudo -i` / `su -`，不要用 `su`"。
