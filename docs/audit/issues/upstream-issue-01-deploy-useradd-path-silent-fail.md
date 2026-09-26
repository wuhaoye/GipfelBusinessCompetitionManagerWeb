> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title` 后的内容）。

---

**title:** `[deploy] PATH 缺少 /usr/sbin 时创建 gipfel 用户静默失败，脚本以 chown 报错中断（Debian 13 真机复现）`

## 环境

| 项 | 值 |
| --- | --- |
| OS | Debian GNU/Linux 13 (trixie)，内核 `6.12.107+deb13-amd64` |
| 分支 / commit | `bugfix-merged` / `371cf57e1dc6248806d00094645a606e85fbe7b8` |
| 受影响文件 | `scripts/deploy-linux.sh`（sha256 `43988fb8e223bb503e034a09154d739b2044ffea26cc30a72776677c378234ed`） |
| 部署方式 | VMware 虚拟机，root 通过 `su`（**未带 `-`**）进入后执行 |
| 复现命令 | `bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx --public-ip <IP>` |

## 问题描述

首次部署时脚本跑到前端构建完成之后突然中止，报的是**权限/属主**错误：

```
[FAIL] 脚本在第 634 行终止（退出码 1）
       命令：chown -R gipfel:gipfel "$INSTALL_DIR/frontend-dist"
```

但真正的原因是**第 298 行创建运行用户失败**，而失败被 `|| true` 吞掉、没有任何输出，导致报错完全指向错误的位置。

## 复现步骤

```bash
# 1) 用一个 PATH 中不含 /usr/sbin 的 root 环境（su 不带 - 时就是这样）
su                      # 注意：不是 su -
echo "$PATH"
# /usr/local/bin:/usr/bin:/bin:/usr/games

command -v useradd      # 无输出 = 未找到（useradd 在 /usr/sbin）
command -v chown        # /usr/bin/chown  ← 可用
command -v systemctl    # /usr/bin/systemctl ← 可用

# 2) 执行脚本第 296-299 行与第 634 行的原样逻辑
if ! id gipfel >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin -U -d /opt/gipfel gipfel || true
fi
id gipfel
chown -R gipfel:gipfel /opt/gipfel/frontend-dist
```

实际输出：

```
bash: line 3: useradd: command not found      ← 被 || true 吞掉，脚本继续
id: 'gipfel': no such user
chown: invalid user: 'gipfel:gipfel'          ← set -e 在此终止，ERR trap 指向 chown
```

## 实际结果

- stdout 里**完全没有**关于 `useradd` 的任何提示；
- 脚本在 `deploy-linux.sh:634` 终止（`set -euo pipefail` + ERR trap）；
- 现场留下"半成品部署"：`.venv`、`db.sqlite3`、`.env`、`staticfiles`、`node_modules`、`frontend-dist` 都已生成（apt/pip/migrate/collectstatic/npm 全部执行成功），但
  - **没有 `gipfel` 用户和组**，
  - **没有 `/etc/systemd/system/gipfel*.service`**，
  - `.env` 仍是 `664 root:root`（第 640 行的 `chmod 600` 未执行），
  - `frontend-dist` 仍是 `root:root`（第 634 行 chown 未生效）。
- 运维视角看到的是"chown 权限问题"，会去排查文件权限/属主，而根因是 PATH 与 `useradd` 缺失。

## 期望结果

- 关键系统命令不可用时，**立即以明确信息中止**（例如"找不到 `useradd`（PATH=…），请确认已安装 passwd 包，并使用 `su -` 或 `sudo` 运行"）；
- 脚本自身不应对 `/usr/sbin` 是否在 PATH 中做隐式假设（`apt-get`/`chown`/`systemctl` 都在 `/usr/bin`，因此环境"看起来正常"）；
- 或者在 `chown -R gipfel:gipfel` 之前断言用户/组已存在，避免以权限错误的形式暴露建用户失败。

## 根因

`scripts/deploy-linux.sh:296-299`：

```bash
if ! id gipfel >/dev/null 2>&1; then
    log "创建专用运行用户 gipfel"
    useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel || true   # ← 失败被吞
fi
```

1. `useradd` 位于 `/usr/sbin`。当脚本在 `su`（非登录 shell）环境下运行时 PATH 为 `/usr/local/bin:/usr/bin:/bin:/usr/games`，`useradd` 不存在 → `command not found`（退出码 127）；
2. `|| true` 把 127 吞成成功，脚本继续执行到 `migrate` / 前端构建；
3. 第 634 行 `chown -R gipfel:gipfel "$INSTALL_DIR/frontend-dist"` 因用户不存在而失败（退出码 1），`set -e` 终止脚本，ERR trap 只报告 chown 那一行。

**同一 PATH 依赖的第二处**：`deploy-linux.sh:869` 的 `nginx -t`（`nginx` 在 `/usr/sbin`）在同样环境下会直接 `command not found` 并以 `nginx -t 失败，请修正` 中止。也就是说即使修好建用户，PATH 问题仍会在第 7 步再次引爆。

> 补充说明：`deploy/README.md` 的示例是 `sudo bash scripts/…`（`sudo` 默认 `secure_path` 含 `/usr/sbin`）。但目标机上执行部署的用户不一定在 `sudoers`（本次环境就是：`journalctl` 记录 `wuhaoye : user NOT in sudoers`），退而使用 `su` 就正好命中。

## 建议修复

### ① 脚本头部统一补齐 PATH

```bash
# deploy-linux.sh / update-from-github.sh / migrate-server.sh 三处都加
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"
```

### ② 用幂等且会失败的 helper 取代 296-299 / 315-318

```bash
ensure_runtime_user() {
    local dir="$1"
    id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 && return 0

    command -v useradd >/dev/null 2>&1 \
        || err "找不到 useradd（PATH=$PATH）；请确认已安装 passwd 包，并使用 su - 或 sudo 运行本脚本"

    if getent group gipfel >/dev/null 2>&1; then
        # 组已存在（例如组内还有其它成员时 userdel 保留了它）→ 用 -g 复用，不要用 -U
        useradd -r -s /usr/sbin/nologin -g gipfel -d "$dir" gipfel
    else
        useradd -r -s /usr/sbin/nologin -U -d "$dir" gipfel
    fi

    id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 \
        || err "创建运行用户/组 gipfel 失败，请手工执行 useradd/groupadd 后重跑"
}
ensure_runtime_user "$INSTALL_DIR"
```

### ③ 在 chown 前加断言（防御性）

```bash
id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 \
    || err "运行用户/组 gipfel 不存在，无法切换文件归属（这通常意味着上面的建用户步骤被跳过或失败）"
```

### ④ 文档补充

`deploy/README.md` 增加一句：**部署必须在 root 登录环境（`sudo -i` 或 `su -`）中执行；不要用 `su`/`su -c`，否则 PATH 缺少 `/usr/sbin`，会以 chown 报错的形式失败。**

## 验证方式（修复后应通过）

在真机（root + 真 `useradd` + 真 systemd）上跑：

```bash
bash code_audit/_repro_gipfel_user_group.sh     # 期望 PASS=16 FAIL=0
```

其中场景 A：无 `gipfel` 用户、无组、安装目录不存在的**全新机器**上，`useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel` 退出码 0、`chown -R gipfel:gipfel` 退出码 0 —— 即"系统里没有 gipfel 用户和组"本身**不是**缺陷，缺陷在于环境 PATH 与被吞掉的错误。

同一份部署脚本在修正环境（`su -` 登录 shell）后于本机复跑，结果：

```
[INFO] 创建专用运行用户 gipfel
[OK]   文件归属已切换为 gipfel（运行时可写 db/uploads/logs），.env 权限收紧为 600
[OK]   gipfel.service 运行中 / gipfel-logviewer.service 运行中 / nginx 已启动
[OK]   后端 /api/health 探针通过（HTTP 200）
[OK]   部署完成！（退出码 0，自检结论：未发现问题）
```
