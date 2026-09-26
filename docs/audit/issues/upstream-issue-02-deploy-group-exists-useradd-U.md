> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title` 后的内容）。

---

**title:** `[deploy] gipfel 组已存在而用户不存在时 useradd -U 必然失败，守卫只检查了用户`

## 环境

| 项 | 值 |
| --- | --- |
| OS | Debian GNU/Linux 13 (trixie) / 亦在 Ubuntu 26.04 复现 |
| 分支 / commit | `bugfix-merged` / `371cf57e1dc6248806d00094645a606e85fbe7b8` |
| 受影响文件 | `scripts/deploy-linux.sh:296-299`、`scripts/update-from-github.sh:315-318` |
| 相关失败点 | `scripts/deploy-linux.sh:634`、`:639`、`scripts/update-from-github.sh:427` |

## 问题描述

部署/升级脚本对运行用户的守卫只检查**用户**，而 `useradd` 带了 `-U`（要求 `gipfel` 这个**组名未被占用**）：

```bash
if ! id gipfel >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel || true
fi
```

当系统上"**组在、用户不在**"时：

- `id gipfel` 判定用户不存在 → 进入创建分支；
- `useradd -U` 因组名已存在而失败：退出码 **9**，`useradd: group gipfel exists - if you want to add this user to that group, use -g.`
- 该失败被 `|| true` 吞掉 → 随后 `chown -R gipfel:gipfel …` 报 `chown: invalid user: 'gipfel:gipfel'`，`set -euo pipefail` 使脚本在 634/639 行（升级脚本为 427 行）中止。

## 复现步骤

```bash
# 0) 前置：无 gipfel 用户，但存在 gipfel 组
groupadd gipfel
getent group gipfel          # gipfel:x:1001:
id gipfel                    # id: 'gipfel': no such user

# 1) 跑脚本原样逻辑
if ! id gipfel >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin -U -d /opt/gipfel gipfel || true
fi
id gipfel
chown -R gipfel:gipfel /opt/gipfel/backend

# 2) 在 set -euo pipefail 下模拟真实脚本控制流
( set -euo pipefail
  trap 'rc=$?; echo "脚本在第 ${LINENO} 行终止（退出码 ${rc}）"; exit $rc' ERR
  if ! id gipfel >/dev/null 2>&1; then useradd -r -s /usr/sbin/nologin -U -d /opt/gipfel gipfel || true; fi
  chown -R gipfel:gipfel /opt/gipfel/backend )
echo "退出码=$?"
```

实际输出：

```
useradd: group gipfel exists - if you want to add this user to that group, use -g.
id: 'gipfel': no such user
chown: invalid user: 'gipfel:gipfel'
chown: invalid user: 'gipfel:gipfel'     ← 子 shell
脚本在第 7 行终止（退出码 1）
退出码=1
```

（`useradd -U` 退出码实测 = **9**。）

## 如何进入"组在、用户不在"状态（真实运维路径，已实测）

1. **组内还有其它成员时删除用户**：`usermod -aG gipfel <other>` 之后 `userdel gipfel` → 系统保留该组：
   `userdel: group gipfel not removed because it has other members.`；
2. 手工 `groupadd gipfel`，或按 `deploy/gipfel.service:8` / `docs/MIGRATION.md:242` 的注释建过用户后又清理；
3. 其它软件包/角色占用了同名组。

> 注意：`USERGROUPS_ENAB yes` 时 `userdel` 会在"组内无其它成员"的情况下连带删组，所以这个状态不是必然出现；一旦出现，脚本没有任何自愈路径。

## 期望结果

- 守卫同时检查用户与组；
- 组已存在时使用 `-g gipfel` 复用该组，而不是用 `-U` 要求新建；
- 建用户失败时立即以明确信息中止，而不是留给后面的 chown 报错。

## 建议修复

```bash
ensure_runtime_user() {
    local dir="$1"
    id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 && return 0

    command -v useradd >/dev/null 2>&1 \
        || err "找不到 useradd（PATH=$PATH）；请确认已安装 passwd 包，并使用 su - 或 sudo 运行本脚本"

    if getent group gipfel >/dev/null 2>&1; then
        useradd -r -s /usr/sbin/nologin -g gipfel -d "$dir" gipfel   # 组已存在 → 复用
    else
        useradd -r -s /usr/sbin/nologin -U -d "$dir" gipfel          # 用户与组一起建
    fi

    id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 \
        || err "创建运行用户/组 gipfel 失败，请手工执行 useradd/groupadd 后重跑"
}
```

`scripts/deploy-linux.sh` 与 `scripts/update-from-github.sh` 共用该 helper（建议放入 `scripts/lib/deploy-common.sh`）。

## 影响

- `update-from-github.sh:427` 在**升级**路径上触发时，数据库迁移已执行完，EXIT trap 会打印回滚指引 —— 属于"新版代码 + 已迁移库 + 服务未重启"的高风险中间态；
- `deploy-linux.sh` 首次部署触发时，`.venv`/`db.sqlite3`/前端产物都已生成，但无 systemd 单元、无运行用户，需要人工判断重跑状态。

## 验证方式

```bash
bash code_audit/_repro_gipfel_user_group.sh   # 期望 PASS=16 FAIL=0，其中场景 B/C 即本 issue
```

| 场景 | 断言 | 实测（Debian 13） |
| --- | --- | --- |
| B 组在、用户不在 | `useradd -U` rc / chown rc / set -e 退出码 | `9` / `1` / `1` |
| C 组内有其它成员时 `userdel` | 用户消失 / 组保留 | 消失 / **保留** |
