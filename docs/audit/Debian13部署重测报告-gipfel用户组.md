# Debian 13 真机重测报告：`gipfel` 用户/组 与部署报错

- 被测主机：`debian` @ `192.168.56.129`（VMware，Debian GNU/Linux 13 trixie，内核 6.12.107+deb13-amd64）
- 被测仓库路径（VM）：`/home/wuhaoye/桌面/GipfelBusinessCompetitionManagerWeb-bugfix-merged`
- 被测脚本指纹：`scripts/deploy-linux.sh` = `43988fb8e223bb503e034a09154d739b2044ffea26cc30a72776677c378234ed`
- 重测时间：2026-09-23 23:26–23:40（CST）
- 结论性质：**只做验证与取证，未修改任何部署脚本/文档**

---

## 0. 结论摘要

| 问题 | 判定 |
| --- | --- |
| "系统里没有名为 `gipfel` 的用户和组时会部署报错" | ❌ **不成立**。全新机器上脚本第 298 行的 `useradd -U` 会同时建出用户和组，`chown` 正常 |
| "本次 VM 上的部署确实报错并中断" | ✅ **成立**，但根因是**执行环境 PATH 缺 `/usr/sbin`**（`su` 未带 `-`）→ `useradd: command not found` 被 `\|\| true` 吞掉 → 错误以 `chown` 的形式在第 634 行爆出 |
| "组在、用户不在时部署会中断" | ✅ **成立**（脚本缺陷，`useradd -U` 在组同名时必定失败） |
| 换正确环境重跑同一脚本 | ✅ **完整部署成功**（退出码 0，三服务 active，站点 200） |

一句话：**用户看到的报错是真的，但报错指向的位置（chown/权限）是假的；真因是 PATH + `\|\| true` 静默吞错。**

---

## 1. 现场取证：上一轮部署停在哪里

### 1.1 journal（决定性证据）

```
Sep 23 23:16:47 debian sudo[6561]: wuhaoye : user NOT in sudoers ; TTY=pts/1 ;
  PWD=/home/wuhaoye/桌面/GipfelBusinessCompetitionManagerWeb-bugfix-merged ; USER=root ;
  COMMAND=/usr/bin/bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx --public-ip 192.168.56.129
```

即：用户在 23:16 尝试 `sudo bash scripts/deploy-linux.sh …`，因 **`wuhaoye` 不在 sudoers 而被直接拒绝**（Debian 安装时设置了 root 密码，安装器就不会把首个用户加入 sudo 组）。此前 22:52–23:21 的那次（成功跑到前端构建的）部署不是通过 sudo 发起的。

### 1.2 `/opt/gipfel` 产物清单（失败点定位）

| 产物 | 状态 | 说明 |
| --- | --- | --- |
| `backend/.venv` | 存在（root） | pip 步骤已执行 |
| `backend/db.sqlite3` | 存在（root, 644） | `migrate` 已执行 |
| `backend/staticfiles` | 存在（root） | `collectstatic` 已执行 |
| `frontend/node_modules`、`frontend/dist` | 存在（root） | `npm install/build` 已执行 |
| `frontend-dist` | 存在（**root:root**） | 第 631–633 行的拷贝完成、**第 634 行 chown 未生效** |
| `backend/.env` | 存在（root:root, **664**） | 第 640 行 `chmod 600` 未执行 |
| `gipfel` 用户/组 | **不存在** | 第 298 行建用户未生效 |
| `/etc/systemd/system/gipfel*.service` | **不存在** | 第 644 行之后的步骤未执行 |
| `/etc/nginx/sites-available/gipfel.conf` | **不存在** | 第 7 步未执行 |
| `/opt/gipfel/_backup` | 不存在 | 首次部署，符合预期 |

→ 脚本停在 **`deploy-linux.sh:634`**：`chown -R gipfel:gipfel "$INSTALL_DIR/frontend-dist"`，`set -euo pipefail` 使其直接终止。

### 1.3 PATH 签名（根因）

```
$ su -c 'echo $PATH'            # 注意：su 未带 -（登录 shell）
/usr/local/bin:/usr/bin:/bin:/usr/games

useradd  -> 未找到          ← /usr/sbin/useradd
apt-get  -> /usr/bin/apt-get   ← 可用
chown    -> /usr/bin/chown     ← 可用
systemctl-> /usr/bin/systemctl ← 可用
```

与现场"apt/pip/migrate/npm 全部成功、唯独建用户失败"**完全吻合**。作为对照，`su - root`（登录 shell）的 PATH 是
`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`，`useradd` 可见。

### 1.4 原样复现（VM 上，去掉 `|| true` 以显示真实退出码）

```
PATH=/usr/local/bin:/usr/bin:/bin:/usr/games
useradd -> 未找到

[deploy-linux.sh:296-299 原样]
  useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel || true
  → /tmp/retest1.sh: line 13: useradd: command not found
  → 失败被 || true 吞掉，脚本继续往下走
  id gipfel -> id: 'gipfel': no such user

[deploy-linux.sh:634 原样]
  chown -R gipfel:gipfel "$INSTALL_DIR/frontend-dist"
  chown: invalid user: 'gipfel:gipfel'
  chown 退出码=1   ← set -e 在这里终止脚本，ERR trap 只报第 634 行
```

同一个 PATH 下，第 869 行的 `nginx -t`（`/usr/sbin/nginx`）也会失败——即 PATH 问题会**二次引爆**，不止建用户这一处。

---

## 2. Debian 13 实测：16 项断言 16/16 PASS

脚本：`code_audit/_repro_gipfel_user_group.sh`（以 root 运行，自动创建/清理临时用户组、目录与临时 systemd 单元）

```
环境：Debian GNU/Linux 13 (trixie) / useradd=/usr/sbin/useradd
PASS=16 FAIL=0
```

| 场景 | 断言 | 实测 |
| --- | --- | --- |
| **A 全新机器**（无用户、无组、安装目录不存在） | useradd rc / chown rc / 属主 | `0` / `0` / `gipfel:gipfel` → **不报错** |
| **B 组在、用户不在** | useradd -U rc / 用户是否存在 / chown rc / set -e 退出码 | `9`（`group gipfel exists`）/ 仍不存在 / `1` / `1` |
| **C 组里有其他成员时 `userdel`** | 用户消失 / 组是否保留 | 消失 / **保留**（`group gipfel not removed because it has other members`） |
| **D1 unit `User=gipfel` 而用户不存在** | 退出状态 | `217/USER`（`Failed to determine credentials for user 'gipfel': Unknown user`） |
| **D2 unit `Group=gipfel` 而组不存在** | 退出状态 | `216/GROUP`（`Failed to determine credentials for group 'gipfel': Unknown group`） |
| **E 不带 `-U` 的 useradd + `USERGROUPS_ENAB=no`** | useradd rc / 组是否创建 / chown rc | `0` / 未创建 / `1`（`invalid group`） |

对照组（同一台 VM，PATH 正确）：

```
$ useradd -r -s /usr/sbin/nologin -U -d /opt/gipfel gipfel
useradd 退出码=0
id gipfel -> uid=997(gipfel) gid=989(gipfel) groups=989(gipfel)
getent group gipfel -> gipfel:x:989:
```

→ **环境本身没问题**，问题只在 PATH 与被吞掉的错误。

---

## 3. 正确环境下重跑真实部署：成功

前置修复（环境侧，非代码）：

```bash
systemctl disable --now apache2     # Debian 13 默认装 apache2 占着 :80，nginx 因此起不来
```

重跑命令（与用户原命令一致，只是处于 `su -` 登录 shell）：

```bash
su -
cd /home/wuhaoye/桌面/GipfelBusinessCompetitionManagerWeb-bugfix-merged
bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx --public-ip 192.168.56.129
```

关键输出：

```
[INFO] 创建专用运行用户 gipfel
[INFO] 发现现有部署 → 备份到 /opt/gipfel/_backup/2026-09-23_233738
[OK]   前端构建完成 → /opt/gipfel/frontend-dist
[OK]   文件归属已切换为 gipfel（运行时可写 db/uploads/logs），.env 权限收紧为 600
[OK]   gipfel.service 运行中
[OK]   gipfel-logviewer.service 运行中
[OK]   nginx 已启动
[OK]   日志查看器站点探针通过（HTTP 302）
[OK]   后端 /api/health 探针通过（HTTP 200）
[OK]   80 端口验证通过：gipfel 站点已生效（非默认欢迎页）
[OK]   部署完成！
  自检结论：未发现问题。
```

部署后核验：

| 项 | 结果 |
| --- | --- |
| `id gipfel` | `uid=997(gipfel) gid=989(gipfel)`；`getent group gipfel` → `gipfel:x:989:` |
| 属主 | `/opt/gipfel`、`uploads`、`logs`、`frontend-dist` = `gipfel:gipfel`；`.env` = `600 gipfel:gipfel` |
| systemd | `gipfel`、`gipfel-logviewer`、`nginx` 均 `active` + `enabled` |
| 后端 | `curl 127.0.0.1:8000/api/health` → `{"code":0,"message":"成功","data":{"status":"ok"}}` |
| 站点 | VM 内 `http://192.168.56.129/` → 200；**Windows 宿主访问 → 200（1143 字节）** |
| 日志查看器 | `http://192.168.56.129:8120/` → 302 |
| 部署日志 | VM `/tmp/deploy-retest.log`（19246 字节，退出码 0） |

---

## 4. 缺陷清单（按严重度）

### [P0] 4.1 `useradd` 失败被 `|| true` 吞掉，错误在 `chown` 处以误导形式爆出

- 位置：`scripts/deploy-linux.sh:296-299`、`scripts/update-from-github.sh:315-318`
- 触发条件：任何使 `useradd` 不可用或失败的执行环境——最典型是 **`su` 未带 `-`**（PATH 无 `/usr/sbin`），其次是 `passwd` 包缺失、组同名冲突。
- 后果：脚本在 `migrate` + 前端构建**完成之后**才死在第 634 行，运维看到的是"权限/属主"错误，无法定位到 `useradd`；此时数据库已迁移、`.venv` 已就绪，属于"半成品部署"，重跑前需要人工判断状态。
- 本次 VM 事故即此条。

### [P0] 4.2 脚本隐式依赖 PATH 含 `/usr/sbin`

- 位置：`scripts/deploy-linux.sh:298`（`useradd`）、`:869`（`nginx -t`）；`scripts/update-from-github.sh:317`、`:840`
- 触发条件：以 `su`（无 `-`）、`su -c`、或任何未重置 PATH 的 root 环境运行；`apt-get`/`chown`/`systemctl` 都在 `/usr/bin` 因此"看起来一切正常"。
- 后果：4.1 的静默失败 + `nginx -t` 的硬失败两处引爆。
- 说明：文档示例写的是 `sudo bash scripts/…`（`sudo` 默认 `secure_path` 含 sbin），但目标机上 `wuhaoye` 不在 sudoers，用户自然改用 `su`，正好踩中。

### [P0] 4.3 "组在、用户不在"时 `useradd -U` 必定失败

- 位置：`scripts/deploy-linux.sh:296-299`、`scripts/update-from-github.sh:315-318`（守卫 `id gipfel` 只查用户，而 `-U` 要求组名未被占用）
- 触发条件（实测 C）：组里还有其它成员时 `userdel gipfel`（组被保留）；或 `groupadd gipfel`；或别的软件包/角色占用同名组。
- 后果：`useradd` rc=9 → 被 `|| true` 吞 → `chown: invalid user: 'gipfel:gipfel'` → 第 634/639 行 / `update-from-github.sh:427` 中止。
- 备注：`update-from-github.sh` 的 EXIT trap 会打印回滚指引（此时数据库已迁移），影响面比首次部署更大。

### [P1] 4.4 `migrate-server.sh` 的建用户与 chown 顺序/参数错误

- 位置：`scripts/migrate-server.sh:339`、`:501`（`useradd` **无 `-U`**）；`:329`（push 模式 chown 在 `:338` 建用户**之前**，且 `2>/dev/null || true` 吞错）；`:505`（pull 模式 chown **无** `|| true`，`set -e` 下会中止）
- 后果：
  - `USERGROUPS_ENAB` 非 `yes` 的机器上只建用户不建组 → `chown: invalid group` + `Group=gipfel` 使服务 `216/GROUP`（实测 E/D2）。
  - push 模式：文件留在 `root:root`，服务能起但写 `db.sqlite3`/`uploads` 时权限拒绝（不报错的隐性故障）。

### [P1] 4.5 文档与单元注释未说明"必须 `su -`/`sudo`"

- 位置：`docs/MIGRATION.md:242-243`（`useradd` 无 `-U` + 无容错 `chown`）、`deploy/README.md`（方案一命令示例）、`deploy/gipfel.service:8-10`、`deploy/logviewer.service:8-10`
- 后果：按文档手工或按直觉用 `su` 执行即踩 4.1/4.2。

### [环境] 4.6 非代码问题（本次一并发现）

| 问题 | 现状 / 处置 |
| --- | --- |
| apache2 占用 `:80` | Debian 13 默认装 apache2，`nginx.service` 22:57:37 报 `bind() to 0.0.0.0:80 failed (98: Address already in use)`。已 `systemctl disable --now apache2`，回退命令：`systemctl enable --now apache2` |
| `wuhaoye` 不在 sudoers | journal 明确记录 `user NOT in sudoers`。要么一律用 `su -`，要么执行 `usermod -aG sudo wuhaoye`（需 root） |
| 仓库缺 `git` | VM 上 `git: 未找到命令`，因此 `update-from-github.sh` 的 git 模式不可用（本次用不到） |

---

## 5. 建议修复方案（本次未落地，供后续实施）

```bash
# ① 三个脚本头部统一补齐 PATH，根治 "su 不带 -" 类环境
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

# ② 用 helper 取代 deploy-linux.sh:296-299 / update-from-github.sh:315-318
#    —— 用户与组都检查；组已存在则用 -g 复用；失败即中止并给出可操作提示
ensure_runtime_user() {
    local dir="$1"
    id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 && return 0
    command -v useradd >/dev/null 2>&1 \
        || err "找不到 useradd（PATH=$PATH）；请确认已安装 passwd 包，并用 su - 或 sudo 运行"
    if getent group gipfel >/dev/null 2>&1; then
        useradd -r -s /usr/sbin/nologin -g gipfel -d "$dir" gipfel
    else
        useradd -r -s /usr/sbin/nologin -U -d "$dir" gipfel
    fi
    id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 \
        || err "创建运行用户/组 gipfel 失败；请手工执行 useradd/groupadd 后重跑"
}
```

③ `migrate-server.sh`：两处 `useradd` 补 `-U`（或改调同一 helper）；push 模式把 `:329` 的 chown 移到建用户之后并去掉 `2>/dev/null`。
④ 文档：`docs/MIGRATION.md:242` 同步；`deploy/README.md` 增补"**必须 `su -` 或 `sudo`，不要用 `su`；先确认 `getent group gipfel` 是否为"组在用户不在"状态**"。
⑤ 回归：`code_audit/_repro_gipfel_user_group.sh` 已覆盖 A–E 五个场景（16 断言），改完直接复跑即可。

同时建议给 `chown` 步加前置断言（如 `id gipfel && getent group gipfel || err …`），避免再次以"权限错误"的面目暴露用户创建失败。

---

## 6. 本次对 VM 的变更与回退

| 变更 | 回退 |
| --- | --- |
| `systemctl disable --now apache2`（让出 `:80`） | `systemctl enable --now apache2` |
| 部署到 `/opt/gipfel`（创建 `gipfel` 用户/组 997:989、装 `gipfel.service`/`gipfel-logviewer.service`、写 `/etc/nginx/sites-available/gipfel.conf`、删除 nginx 默认站点） | `systemctl disable --now gipfel gipfel-logviewer`；`rm /etc/systemd/system/gipfel*.service /etc/nginx/sites-available/gipfel.conf`；`userdel gipfel`（如需） |
| 数据库/配置备份 | `/opt/gipfel/_backup/2026-09-23_233738` |
| 部署日志 | VM `/tmp/deploy-retest.log`（保留） |
| 临时测试脚本 | 已清理（`/tmp/retest1.sh`、`/tmp/harness.sh`、`/tmp/precheck.sh` 等） |
| 本机仓库 | 仅新增 `code_audit/_repro_gipfel_user_group.sh`（回归脚本），**未改动任何部署脚本/文档** |

---

## 7. 复跑方法

```bash
# 在 Linux（真 root，真 useradd，真 systemd）上执行
bash code_audit/_repro_gipfel_user_group.sh     # 期望 PASS=16 FAIL=0
# WSL 环境示例：
wsl -u root -e bash -lc "sed -i 's/\r$//' /mnt/c/.../code_audit/_repro_gipfel_user_group.sh; \
                         bash /mnt/c/.../code_audit/_repro_gipfel_user_group.sh"
```

脚本自带清理（临时用户 `gipfel`/`gtmp`、`/opt/gipfel-repro*`、临时 systemd 单元），不影响 `/opt/gipfel` 真实部署。
