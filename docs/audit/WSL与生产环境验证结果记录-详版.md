# WSL 与生产环境验证结果记录

> 按 `WSL与生产环境验证操作手册.md` §5 模板填写。
> 执行时间：2026-09-14（本机 Windows + WSL2 Ubuntu-26.04）

## 环境

| 项 | 值 |
| --- | --- |
| 发行版 | Ubuntu 26.04 LTS (Resolute Raccoon)，WSL2 |
| bash | GNU bash 5.3.9(1)-release (x86_64-pc-linux-gnu) |
| PID 1 | `systemd`，`systemctl is-system-running` → `running` |
| python3 | 3.14.4，`sqlite3` 模块 3.46.1 |
| 仓库 | 分支 `bugfix-merged`，HEAD `179712f` |
| 仓库路径（WSL 视角） | `/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb` |
| 生产服务器 | `43.142.77.225`（来自 `deploy/README.md`），22/80/8120 均可达 |

执行前提：沙箱默认模式下 `wsl.exe` 报
`Wsl/EnumerateDistros/Service/E_ACCESSDENIED` 与 `Wsl/Service/E_ACCESSDENIED`
（与手册 §1.2 描述一致），提权到 `danger-full-access` 后正常。

---

## 第一层（WSL 真实 Linux）

- [x] **§2.1 `code_audit/_wsl_verify.sh` → `PASS=44 FAIL=0`（退出码 0）**
- [x] **§2.2 分段明细全部符合期望**（见下）
- [x] **§2.3 在 Linux 侧文件系统上复跑 → 脚本 36 项 + 手工补齐段 1 的 8 项 = 44/44**
- [x] **§3 行尾前置检查：工作区 CRLF 文件数 = 0**
- [x] **§2.5 Python 侧回归四项全部通过**（Windows 与 WSL 双侧一致，见下）

### §2.1 / §2.2 分段明细（原始输出 `code_audit/_wsl21_out.txt`）

| 段 | 内容 | 期望 | 实测 |
| --- | --- | --- | --- |
| 0 | 环境自述 | 打印版本 | bash 5.3.9 / Python 3.14.4 / init=systemd / systemd=running / sqlite3 3.46.1 |
| 1 | 8 个被跟踪 `.sh` 的 `bash -n` | 8/8 OK | **8/8 PASS** |
| 2 | `tests/deploy_public_ip_test.sh` 三种 cwd | 均 `PASS=36 FAIL=0` | 3/3 PASS（cwd=`tests`、仓库根、`/tmp`） |
| 3 | `deploy-common.sh` 纯函数 | `/tmp/a/c`、`/opt/gipfel`、`9000`、`8120` | 4/4 PASS |
| 4 | `snapshot_sqlite_consistent` 对活 WAL 库 | 成功且 `integrity=ok` | PASS；`snap.sqlite3: integrity=ok rows=3000`；对照 `plain.sqlite3`（`cp -a` 活库）→ `no such table: t` |
| 5 | `migrate-server.sh` 退出码 + dry-run | 0/2/1/2；dry-run 0、无连接、无残留 | 8/8 PASS |
| 6 | `quick-sync.sh` | 4 项 | 4/4 PASS |
| 7 | `deploy-linux.sh` | 4 项 | 4/4 PASS |
| 8 | `update-from-github` / `verify-migration` | 非崩溃；无 `((PASS++))` | 3/3 PASS（`--help` 分别以 0 / 1 结束） |
| 9 | `systemd-analyze verify`（真 systemd） | 无指令级错误 | 4/4 PASS：无指令级错误；`gipfel.service` 已移除 Unix socket；运行目录 0750；`logviewer.service` `UMask=0027` |
| 10 | `print_rollback_hint` | 六步齐全 | 5/5 PASS |
| — | **汇总** | `PASS=44 FAIL=0` | **`PASS=44 FAIL=0`，退出码 0** ✅ |

附带（预期噪音，非失败）：§3 用例把 `LOG_VIEWER_PORT=abc` 喂给 `deploy-common.sh` 时，
stderr 输出 `[deploy][warn] LOG_VIEWER_PORT=abc 非法（需 1-65535 整数），回退默认 8120` ——
这正是用例要验证的行为。

### §2.3 在 Linux 侧文件系统上复跑

按手册 §2.3 的命令执行：`git archive HEAD | tar -x -C /tmp/gipfel-repo`，
并把未跟踪的 `code_audit/` 单独拷入，然后以 `REPO=/tmp/gipfel-repo` 复跑验证脚本。

一点需要更正手册措辞的实测事实：手册说 `/tmp` 是"真 Linux 文件系统"，
`stat -f` 实测各挂载点为：

| 挂载点 | 文件系统 | 可写 |
| --- | --- | --- |
| `/tmp` | **tmpfs** | 是 |
| `/root` | **ext2/ext3**（实为 ext4） | 是 |
| `/var/tmp` | **ext2/ext3** | 是 |
| `/mnt/c`（仓库所在） | **v9fs**（DrvFs） | 是 |
| `/` | ext2/ext3 | 是 |

即 §2.3 用的 `/tmp` 是 **tmpfs 而非 ext4**。结论不受影响 —— tmpfs 同样是原生 POSIX 语义，
且实测正是 §0 坑 3 关心的两点：

```
chmod 600 → -rw-------      大小写：敏感（不同文件）
chmod 750 → -rwxr-x---
```

所以 §2.3 确实达到了"避开 DrvFs 权限位失真"的目的；但"tmpfs 而非 ext4"值得在手册里写准。
若要真 ext4，把 `WORK_REPO` 换到 `/root/gipfel-repo` 或 `/var/tmp/gipfel-repo` 即可
（实测这两处都是 ext4 且可写）。

实测结果（**一个重要陷阱，见下**）：

- `git archive` 出来的树里 `.sh` **CRLF 文件数 = 0**（`.gitattributes` 生效）
- `bash -n` 全部通过 → **8/8 PASS**
- `REPO=/tmp/gipfel-repo bash .../_wsl_verify.sh` → **`PASS=36 FAIL=0`，退出码 0**

> ⚠️ **§2.3 的 36 不是"少了 8 项失败"，而是段 1 在这个环境里静默跳过。**
> 原因：`git archive` 出来的树**没有 `.git`**，而验证脚本段 1 用的是裸 `git ls-files '*.sh'`，
> 在 archive 树里直接 `fatal: not a git repository`，于是段 1 **一条都不执行**（不是 FAIL，是不计入）。
> 所以 `REPO=/tmp/gipfel-repo` 的正确口径是：
>
> ```
> 脚本自身 = 36（段 2–10）
> 段 1 需另算 = 8（用原仓库的清单，逐个 bash -n archive 树里的文件）→ 8/8 PASS
> 合计 = 44 ✅
> ```
>
> 手工补齐段 1 后与 §2.1 的 44 项**逐段一致**。手册 §2.3 只写了"应看到 PASS=44"，
> 但按它自己的命令跑必然只得到 36 —— 建议手册在此处加一句说明，
> 否则下一个执行者会以为复跑少了 8 项。
> 顺带：archive 树里 **`docs/` 是齐的（11 个文件）**，所以 §2.5 若改在 archive 树里跑不会踩 Z17 那个坑。

> 内层计数勿混淆：复跑日志里 `deploy_public_ip_test.sh` 自己也报 `PASS=36 FAIL=0`（段 2），
> 这跟"脚本自身 36 项"是完全不同的两回事 —— 数字撞在一起纯属巧合。

### §3 行尾与传输前置检查

- Windows 工作区（`/mnt/c`，v9fs）上 8 个被跟踪 `.sh`：**CRLF 文件数 = 0**
- `bash -n scripts/deploy-linux.sh` → **语法 OK**（若为 CRLF 会报 `$'\r': command not found`）
- `.gitattributes` 规则生效：`*.sh text eol=lf`

**额外的一致性核验**：用手册 §2.4 的内嵌脚本重新抽取并与仓库里的脚本比对：

```
sha256 _wsl_verify.sh            = 6FD462363A7045B52C2CF022E8292625A9CB0EED9F4ED427966251DE28F0B91D
sha256 _wsl_verify_from_doc.sh   = 6FD462363A7045B52C2CF022E8292625A9CB0EED9F4ED427966251DE28F0B91D
```

两者**逐字节相同**（8617 字节 / 145 行）→ 手册 §2.4 的内嵌脚本与仓库实际脚本没有漂移，
"脚本不在时可原样重建"这句话成立。

### §2.5 Python 侧回归

Windows 侧基线（`backend/.venv`，Python 3.14.6 / Django 5.0.14）**四项全部与手册期望一致**：

| 命令 | 期望（手册 §2.5） | 实测 | 结论 |
| --- | --- | --- | --- |
| `python manage.py test apps` | `Ran 153 tests ... OK` | **`Ran 153 tests in 66.219s` → `OK`**（exit 0） | ✅ |
| `python manage.py test tests_fix_verify` | `Ran 280 tests ... OK` | **`Ran 280 tests in 148.117s` → `OK`**（exit 0） | ✅ |
| `python tests/big_number_smoke.py` | `PASS=22 FAIL=0` | **`PASS=22 FAIL=0`**（exit 0） | ✅ |
| `python tests/sqlite_decimal_roundtrip.py` | `PASS=10 FAIL=0`，且不碰 `backend/db.sqlite3` | **`PASS=10 FAIL=0`**（exit 0） | ✅ |

**"不碰真实库"已用哈希证明**：

```
backend/db.sqlite3 sha256 before = F6256FDAEC5572D37C4A788D6B0B6E2C7490BDFA0581B2BC3D425A436F9BB440
backend/db.sqlite3 sha256 after  = F6256FDAEC572D37... （完全相同）
unchanged = True
```

WSL 内复跑（手册原文要求的那一层）**四项也全部与期望一致**，与上表互为交叉验证：

```
===== manage.py test apps =====
Ran 153 tests in 14.213s
OK                                    (apps rc=0)

===== manage.py test tests_fix_verify =====
Ran 280 tests in 34.159s
OK                                    (fv rc=0)

===== tests/big_number_smoke.py =====
结果：PASS=22 FAIL=0                    (smoke rc=0)

===== tests/sqlite_decimal_roundtrip.py =====
结果：PASS=10 FAIL=0
Windows 侧 backend/db.sqlite3 md5: before=aa4855c38792e1a0f7129e5eb1e1471d
                                   after =aa4855c38792e1a0f7129e5eb1e1471d
OK：未触碰真实库
```

即同一批用例在 **Windows / Python 3.14.6 / Django 5.0.14** 与
**Ubuntu 26.04 / Python 3.14.4** 两侧结论一致：`153 OK` / `280 OK` / `22+0` / `10+0`。

> 过程记录见「异常与原始输出」5：首次 WSL 复跑时 `tests_fix_verify` 报了 **3 个 error**，
> 排查后确认是**复跑脚手架**没把 `docs/` 拷过去（Z17 用例要读 `docs/*.md`），
> **不是产品缺陷**；把 `docs/` 一并拷入后 280 项全绿。

---

## 第二层（真机）—— **部分完成：只有 §4.4 的公开探针跑了，其余 7 项全部未执行**

- ✅ **已完成**：§4.4 的两个功能探针（只需公开 HTTP，无需登录）
- ❌ **未执行**：§4.1、§4.2、§4.3、§4.5、§4.6、§4.7、§4.8 —— 都需要 SSH 登录到服务器

事实依据（均为只读检查）：

- `%USERPROFILE%\.ssh` 目录**为空**：无私钥、无 `config`、无 `known_hosts`
- `ssh-add -l` → `Error connecting to agent: No such file or directory`（无 agent 在跑）
- 因此 `sudo`/`systemctl`/`journalctl`/`/opt/gipfel` 全部不可达

> §4.3 与 §4.8 已在提问中确认「允许全部照做（含改动线上服务）」，
> 但**授权不等于可执行**：没有登录凭据，这两项同样一步都做不了。
> 一旦提供 SSH 用户名 + 私钥（或密码），可直接从待办清单继续。

### 已完成的真机部分：§4.4 公开端点探针（只读、无需登录）

| 检查 | 命令 | 实测 | 期望 | 结论 |
| --- | --- | --- | --- | --- |
| API 健康 | `curl http://43.142.77.225/api/health` | `{"code":0,"message":"成功","data":{"status":"ok"}}`，**http=200** | 2xx/3xx | ✅ |
| nginx 主站 | `curl -I http://43.142.77.225/` | **http=200**，`Server: nginx`，`Content-Length: 534`，`Last-Modified: Thu, 10 Sep 2026 12:19:46 GMT` | 2xx/3xx | ✅ |
| 日志查看器公网块 | `curl -I http://43.142.77.225:8120/` | **http=302** → `Location: http://43.142.77.225/` | 2xx/3xx（302 属 3xx） | ✅ |

§4.4 的期望「均为 2xx/3xx」**两个探针都满足**。
附带印证了几项设计意图：

- 安全响应头已按 `deploy/nginx-gipfel.conf` 注入：
  主站与 8120 块均有 `X-Content-Type-Options: nosniff`、`X-Frame-Options: SAMEORIGIN`、
  `Referrer-Policy: strict-origin-when-cross-origin`、`Permissions-Policy`、`X-XSS-Protection: 1; mode=block`
- 8120 未带 token 访问被**网关拦回主站**（302 → `/`，并下发 `lv_csrftoken`），
  与 `deploy/README.md`「仅按钮跳转、直接输入网址进不去」的说明一致
- nginx 反代日志查看器时 `X-Frame-Options: DENY` 覆盖为 `SAMEORIGIN`，
  说明 `add_header` 在 location 级的覆盖行为与文档一致

> 注意：`http://43.142.77.225/api/health`（经 nginx，80 端口）返回 200；
> 手册 §4.4 的写法即为此形态。服务器本机 `127.0.0.1/api/health` 无法从外部验证。

### 第二层待办清单（拿到 SSH 后照此继续）

- [ ] **4.1** 脚本自检：`cd /opt/gipfel`（或 `/opt/GipfelBusinessCompetitionManagerWeb`）→ 行尾无 CRLF；`bash -n scripts/*.sh` 通过；新开关可见；依赖里除 `ufw`（可缺）外都在；`.venv/bin/python -V`
- [ ] **4.2** systemd：`systemd-analyze verify` exit=0；`gipfel` 的 `UMask=0027`、`RuntimeDirectoryMode=0750`、`ReadWritePaths` 含 uploads/logs/db.sqlite3/`/run/gipfel`/`/var/log/gipfel`；`gipfel-logviewer` 的 `ReadWritePaths` **不含** db.sqlite3/uploads/`/run/gipfel`；`/run/gipfel` 等目录 `drwxr-x---`；`/run/gipfel/gipfel.sock` **不存在**；`ss -ltnp` 只绑回环（`127.0.0.1:8000` daphne、`127.0.0.1:8121` daphne-logviewer、`0.0.0.0:8120` nginx）
- [ ] **4.3** 部署脚本行为（**改动线上，需维护窗口**）：`deploy exit=0`；`/tmp/deploy.log` 中**不含** `SEED_ADMIN_PASSWORD` 明文；`--print-seed-password` 在非 TTY 下明确告警
- [x] **4.4** 探针：logviewer **http=302**，api/health **http=200** ✅（见上）
- [ ] **4.5** 备份自洽：`ls -dt /opt/gipfel/_backup/*/ | head -1` 的 `db.sqlite3` → `integrity_check=ok` 且表数量 > 0
- [ ] **4.6** SSH/rsync：`migrate-server.sh --dry-run` exit=0、无 `/tmp/gipfel-migration-*` 残留；退出码语义 `--help`/错参数/缺 mode = 0/2/1；`quick-sync.sh` 非 22 端口 + `--ssh-key`/`--ssh-known-hosts` 提示正确
- [ ] **4.7** 诊断脚本：`tests/gipfel-logviewer-diag.sh` 输出的 `LOGVIEWER_SECRET_KEY` 为掩码形态，原文不出现在 `/tmp/diag.log`
- [ ] **4.8** 升级 + 回滚演练（**改动线上，需维护窗口**）：记录 `HEAD` 与 `db.sqlite3` 回滚点 → `update-from-github.sh` → 若失败照抄脚本打印的六步回滚指引 → `systemctl is-active gipfel gipfel-logviewer` + `curl /api/health`

---

## 异常与原始输出

### 1. 沙箱拦 WSL（预期，已按手册 §1.2 处理）

```
PS> wsl.exe -l -v
拒绝访问。
错误代码: Wsl/EnumerateDistros/Service/E_ACCESSDENIED
```

提权到 `danger-full-access` 后：

```
PRETTY_NAME="Ubuntu 26.04 LTS"
VERSION_CODENAME=resolute
```

→ 手册 §1.2 的判断（"不是没装 WSL，是沙箱拦了命名管道"）**完全正确**。

### 2. §2.5 首次尝试失败：WSL 内建 venv 缺 `ensurepip`

```
The virtual environment was not created successfully because ensurepip is not
available.  On Debian/Ubuntu systems, you need to install the python3-venv
package using the following command:

    apt install python3.14-venv

You may need to use sudo with that command.
Failing command: /tmp/gipfel-py/backend/.venv/bin/python3
```

且 `apt-get` 因非 root 报：

```
E: Could not open lock file /var/lib/apt/lists/lock - open (13: Permission denied)
E: Unable to lock directory /var/lib/apt/lists/
```

**处理与结论**（两处都值得写进手册）：

1. **非 root**：WSL 默认用户没有免密 `sudo`，普通用法的 `sudo apt-get` 会卡在密码；
   改用 `wsl -d Ubuntu-26.04 -u root -- bash <脚本>` 即可直接以 root 执行，
   不需要在 Windows 侧配 sudoers。
2. **包名**：Ubuntu 26.04 上 `python3-venv` 是**元包**，实际依赖 `python3.14-venv`；
   装 `python3-venv` 会把 `python3.14-venv` 一并带进来。所以手册 §1.3 的
   `apt-get install -y python3-venv python3-pip` 本身是对的，
   **但必须真的装上**——本次失败纯粹是因为上一步权限不够、包根本没装成。
   装完后实测：`python3-venv` / `python3.14-venv` / `python3-pip` 均 `install ok installed`，
   `ensurepip` 可用。

### 3. §2.5 第二次尝试失败：`files.pythonhosted.org` 遭 TLS 中间人

```
WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None))
after connection broken by 'SSLError(SSLCertVerificationError(1,
'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate
(_ssl.c:1081)'))': /packages/c0/93/.../Django-5.0.14-py3-none-any.whl

error: incomplete-download
× Download failed after 6 attempts because not enough bytes were received (2.9 MB/8.2 MB)
note: This is an issue with network connectivity, not pip.
```

**定性**：这是**本机网络环境**问题（对 PyPI 的 TLS 被中间人替换成自签证书），
与本仓库代码、脚本、被测逻辑**无关**。按手册 §1.3 / `deploy/README.md` 的
「受限网络」口径换国内镜像重试（阿里云 → 清华 TUNA 兜底，带 `--trusted-host`）。

### 4. v9fs 上 `cp -r backend` 会拖很久

第一版 §2.5 脚本用 `cp -r backend` 再 `rm -rf .venv`，
在 `/mnt/c`（v9fs）上要**先白搬** `backend/.venv` 里上万个文件，实测十余分钟仍未完成。
改成 `rsync -a --exclude=.venv --exclude=db.sqlite3 --exclude=uploads --exclude=logs ...`
后，4.8M backend + 820K tests **秒级完成**。凡是要把仓库从 `/mnt/c` 拷到 Linux 侧，
都应带 `--exclude`（手册 §2.3 用 `git archive` 天然规避了这一点，因为它只含被跟踪文件）。

### 5. WSL 复跑 `tests_fix_verify` 报 3 个 error —— **脚手架问题，不是产品缺陷**

首次 WSL 复跑（只拷了 `backend/` + `tests/`）时：

```
ERROR: test_tutorial_doc_counts_are_consistent
(tests_fix_verify.test_z17_doc_sheet_counts.Z17DocConsistencyTests.test_tutorial_doc_counts_are_consistent)
FileNotFoundError: [Errno 2] No such file or directory:
    '/tmp/gipfel-py/docs/比赛Excel建包教程.md'

Ran 280 tests in 33.810s
FAILED (errors=3)
```

3 个 error 全是 `FileNotFoundError`，指向 `/tmp/gipfel-py/docs/` 下的三份文档。

**定性**：`tests_fix_verify` 的 Z17 用例会**直接读仓库根下的 `docs/*.md`**，
校验「文档里写的 sheet/字段计数」与代码一致。复跑脚手架只同步了 `backend/` 与 `tests/`，
`docs/` 缺失才导致报错 —— 与 `bugfix-merged` 的代码无关（同批用例在 Windows 全绿即为反证）。

**修正后**（`rsync docs/` 一并拷入，372K）：

```
===== manage.py test tests_fix_verify =====
Ran 280 tests in 34.159s
OK
```

**给手册的建议**：§2.5 若在 Linux/WSL 侧复跑，工作目录必须是**完整仓库树**
（至少含 `backend/`、`tests/`、`docs/`），不能只拷 `backend/`；
否则会看到 3 个与代码无关的 `FileNotFoundError`，容易被误判成回归失败。

### 6. 用 `-u root` 跑验证脚本会让段 1 静默消失（我踩到了，已修正）

为了跑 §2.5 的 `apt`，我一度改用 `wsl -d Ubuntu-26.04 -u root`。**同一个 `-u root` 也污染了 §2.1 的复跑**：

```
== 1. 全部 shell 脚本语法（真 Linux bash） ==
fatal: detected dubious ownership in repository at
'/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb'
To add an exception for this directory, call:
	git config --global --add safe.directory ...
                                                ← 段 1 一条都没跑
...
════ 汇总 ════
  PASS=36 FAIL=0                                ← 静默变成 36
```

**机理**：仓库在 `/mnt/c`（v9fs）上属主是 Windows 用户 `wuhao`；root 去跑 `git ls-files` 时
git 报 `dubious ownership` 并**非零退出**，脚本段 1 的 `for f in $(git ls-files '*.sh')` 展开为空，
于是**一条不执行、也不计 FAIL**，汇总就从 44 掉到 36 —— **没有任何红色提示**，极易误判。

**修正**：验证脚本必须以 **WSL 默认用户 `wuhao`** 执行（`wsl -d Ubuntu-26.04 -- bash ...`，
不加 `-u root`）。只有 §2.5 里需要 `apt-get` 的那一步才用 root，见第 2 条。
若确实要用 root，先 `git config --global --add safe.directory <仓库路径>`。

> 这条（root 下 `git ls-files` 静默归零）和 §2.3 的 36（archive 树无 `.git`）
> 是**两个独立的坑，都会把 44 变成 36**，且都不报错。
> 复跑时看到 36 请先查这两处，再怀疑代码。

### 7. 验证过程未污染仓库

`git status --porcelain` 中**没有任何被跟踪文件的改动**（只有原本就未跟踪的
`code_audit/`、手册与两份报告），说明 §2.1–§2.3 的 44 项检查确实零副作用。
`_wsl_verify.sh` 与从手册抽取的副本 sha256 相同，未被改写。
另外 §2.1 段 5 声明的「`--dry-run` 无临时目录残留」也在脚本外独立复核过：
`/tmp/gipfel-migration-*` 无残留。

---

## 给手册的修订建议（按优先级）

| # | 位置 | 现状 | 建议 |
| --- | --- | --- | --- |
| 1 | §2.3 | 只写"期望 `PASS=44`" | 按手册自己的命令跑**必然只得 36**（archive 树无 `.git`，段 1 静默跳过）。补一句"脚本自身 36 + 段 1 手工 8 = 44"，否则下一位执行者会误判少了 8 项 |
| 2 | §2.1 / §1.2 | 未提执行身份 | 明确"必须用 WSL **默认用户**跑，不要加 `-u root`"：root 下 git 报 `dubious ownership`，段 1 静默归零，汇总变 36 且**无任何红色提示** |
| 3 | §1.3 / §2.5 | `sudo apt-get install -y python3-venv python3-pip` | WSL 默认用户没有免密 sudo，`sudo` 会卡住；建议直接写 `wsl -d Ubuntu-26.04 -u root -- bash <脚本>` 或先在 WSL 内配好 sudoers。包名本身没问题（26.04 上 `python3-venv` 会带出 `python3.14-venv`） |
| 4 | §2.3 措辞 | 称 `/tmp` 为"真 Linux 文件系统" | 实测 `/tmp` 是 **tmpfs**（不是 ext4）。tmpfs 同样是原生 POSIX（`chmod`/大小写都真），结论不受影响；若要 ext4，用 `/root` 或 `/var/tmp` |
| 5 | §2.5 | 只说"在 WSL 里跑" | 必须给出**完整仓库树**（`backend/` + `tests/` + **`docs/`**）；只拷 `backend/` 会因 Z17 用例读不到 `docs/*.md` 而报 3 个 `FileNotFoundError`，极易误判成回归失败 |
| 6 | §1.3 / §2.5 | 未提镜像 | 本机对 `files.pythonhosted.org` 存在 TLS 中间人（`self-signed certificate`，Django 轮子下到 2.9MB/8.2MB 断）。建议补一句"受限网络换国内镜像 + `--trusted-host`"。另外从 `/mnt/c` 拷到 Linux 侧**务必带 `--exclude`**（`cp -r backend` 会白搬 `.venv` 上万个文件） |
| 7 | §4 开头 | 未写凭据前提 | 建议明确"第二层需要能登录服务器的 SSH 凭据"；本机 `.ssh` 为空、无 agent，导致 §4 除 §4.4 外全部无法开工 |

### 本轮实际可交付的结论边界

- **第一层（WSL 真实 Linux）：完整通过** —— §2.1 44/44、§2.3 44/44（36+8）、§3 CRLF=0、§2.5 四项双侧一致。
- **第二层（真机）：仅 §4.4 通过**（`api/health`=200、logviewer=302），其余 7 项**未开工**，原因是缺 SSH 凭据，非代码问题。
- 因此**不能**据此宣称"生产环境已验证通过"；生产侧的 systemd 权限收紧、Unix socket 移除、端口只绑回环、
  备份自洽性、口令不落日志这些关键项，**仍未在真机上确认过**。
