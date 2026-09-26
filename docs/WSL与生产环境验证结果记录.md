# WSL 与生产环境验证结果记录

> **配套文档**：操作步骤见《[WSL与生产环境验证操作手册.md](WSL与生产环境验证操作手册.md)》；
> 缺陷清单见《分支代码缺陷审计报告.md》§8。
>
> **记录时间**：本次会话（2026-09-14）
> **被验证对象**：分支 `bugfix-merged`，HEAD `179712f`（`master` 未被改动，仍为 `97e117e`）
> **结论一句话**：WSL 真实 Linux 复验 **44/44 通过**；过程中发现并修复 **1 个真实缺陷**（X-27，见 §3）；
> **真机（生产服务器）部分尚未执行**，待办清单见 §4。

---

## 1. 验证环境（实测值，非推断）

| 项 | 实测值 | 获取方式 |
| --- | --- | --- |
| 发行版 | `Ubuntu 26.04 LTS` | `. /etc/os-release` |
| 内核 | `6.18.33.2-microsoft-standard-WSL2` | `uname -r` |
| WSL 版本 | WSL2，distro 状态 `Running` | `wsl.exe -l -v` |
| **PID 1** | **`systemd`** | `ps -p 1 -o comm=` |
| systemd 状态 | `running` | `systemctl is-system-running` |
| bash | `GNU bash, version 5.3.9(1)-release (x86_64-pc-linux-gnu)` | `bash --version` |
| python3 | `3.14.4`，`sqlite3` 模块 `3.46.1` | `python3 -V` / `import sqlite3` |
| 可用工具 | bash、python3、rsync、ssh、curl、git、sudo、ss、systemctl、systemd-analyze | `command -v` |
| **缺失工具** | nginx、ufw、sqlite3(CLI)、node、npm、pip3、python3-venv | `command -v` |
| 仓库路径（WSL 视角） | `/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb` | — |
| 对照环境（非 Linux） | Git for Windows bash 5.3.15（**不能替代** Linux 验证，见 §3） | — |

> **关于"能否用 WSL"的一条更正**：此前多轮报告"本机 WSL 未安装 / `E_ACCESSDENIED`"。
> 真实原因是**受限沙箱拦了命名管道**（`Win32 error 5`），并非没有 WSL。
> 提权到 `danger-full-access` 后 `wsl.exe -l -v` 正常列出 `Ubuntu-26.04`。

---

## 2. 第一层：WSL（真实 Linux）验证结果

**执行命令**

```powershell
$env:WSL_UTF8='1'
wsl.exe -d Ubuntu-26.04 -- bash /mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb/code_audit/_wsl_verify.sh
```

**结果：`PASS=44 FAIL=0`（退出码 0）**

| # | 检查段 | 结果 | 关键实测输出 |
| --- | --- | --- | --- |
| 0 | 环境自述 | ✅ | bash 5.3.9 / python3 3.14.4 / init=systemd / systemd=running |
| 1 | 8 个被跟踪 `.sh` 的 `bash -n` | ✅ 8/8 | 全部 OK（**前提是 LF 行尾**，见 §3） |
| 2 | `tests/deploy_public_ip_test.sh` 三种 cwd | ✅ 3/3 | 均为 `结果：PASS=36 FAIL=0` |
| 3 | `deploy-common.sh` 纯函数 | ✅ 4/4 | `absolutize_dir ./a/b/../c`→`/tmp/a/c`；`/opt/gipfel//x/..`→`/opt/gipfel`；`log_viewer_port` 9000 / 非法回退 8120 |
| 4 | `snapshot_sqlite_consistent`（活的 WAL 库） | ✅ | 快照 `integrity=ok rows=3000`；**对照 `cp -a` 活库 → `no such table: t`** |
| 5 | `migrate-server.sh` 退出码 + `--dry-run` | ✅ 8/8 | `--help`=0、未知参数=2、缺 `--mode`=1、`--ssh-port abc`=2；dry-run 退 0、有预演输出、未真实连接、`/tmp/gipfel-migration-*` 残留数不变 |
| 6 | `quick-sync.sh` | ✅ 4/4 | 不存在的推送源被拒（rc=1）；SSH 选项被识别；给了 known_hosts 不再告警 `accept-new`；非法端口 rc=2 |
| 7 | `deploy-linux.sh` | ✅ 4/4 | 帮助含 `--print-seed-password`、`--allow-partial`；口令守卫 2 处；收尾"无条件 ok"行数 = 0 |
| 8 | `update-from-github.sh` / `verify-migration.sh` | ✅ 3/3 | `--help` 非崩溃退出；计数器已不是 `((PASS++))` |
| 9 | **`systemd-analyze verify`**（真 systemd） | ✅ | 无指令级错误；`gipfel.service` 无 `-u …gipfel.sock`；`RuntimeDirectoryMode=0750`；`UMask=0027` |
| 10 | `print_rollback_hint` | ✅ 5/5 | 六步命令齐全（停服务 / 恢复库 / 恢复上传配置 / 回退代码 / 重装依赖前端 / 起服务健康检查） |

**原始输出存档**：`code_audit/_WSL_verify_out.txt`（含 ANSI 颜色码，去色后阅读即可）

---

## 3. 本轮发现并修复的缺陷

### X-27（新发现，已修复）：Windows 检出会让全部部署脚本在 Linux 上无法执行

**定级**：P1（阻断级 —— 脚本在服务器上完全跑不起来）

**现象与实测**（WSL Ubuntu 26.04 / GNU bash 5.3.9）

```
$ bash -n scripts/deploy-linux.sh
scripts/deploy-linux.sh: line 16: $'\r': command not found
scripts/lib/deploy-common.sh: line 26: syntax error near unexpected token `$'{\r''
```

| 对象 | CRLF 行数 | `bash -n` |
| --- | --- | --- |
| 工作区（Windows 检出，`core.autocrlf=true`） | 802 / 285 / 534 / 315 / 709 / 223 / 167 / 91 | **8/8 FAIL** |
| 同一批文件的 LF 副本 | 0 | **8/8 OK** |
| git 对象库（`git show HEAD:<f> | grep -c $'\r$'`） | 0 | — |

**影响面**：正常 `git clone` 到 Linux 不受影响（对象库本来就是 LF）；但**一切经 Windows 传输的路径**都会中招：

- `deploy/README.md` 里"服务器完全连不上 GitHub 时打个 tar 包传上去"的流程；
- zip / scp from Windows；
- 通过 WSL 的 `/mnt/c` 直接调用（本轮第一次跑就是这样炸的）。

而 `$'\r': command not found` 这类报错对不熟悉行尾问题的人极难定位，容易被误判成"脚本逻辑坏了"。

**同时更正一条此前口径**：之前多轮报告的"8 个 shell 脚本 `bash -n` 全部通过"，是在
**Git for Windows 的 bash** 下跑的 —— 它容忍 CRLF，因此那条结论**不能**证明 Linux 可用。
本轮改用 WSL 的 GNU bash 才暴露出来。

**修复**：提交 `179712f`

```
.gitattributes （新增）
  *.sh text eol=lf        # 附注释说明原因与实测数据
工作区 .sh 重新检出为 LF；git ls-files --eol 现在全部为 i/lf w/lf attr/text eol=lf
```

对象库本来就是 LF，所以该规则**不产生任何文件内容变更**，只是把"检出必须是 LF"固定下来，
防止后续贡献者提交 CRLF 的 shell 脚本。

**修复后复验**：`bash -n` 8/8 OK；`code_audit/_wsl_verify.sh` → `PASS=44 FAIL=0`。

### 附带记录：工具链层面的同类陷阱（不是本仓库缺陷，但会反复咬人）

Windows 上 `Path.write_text(text, encoding="utf-8")` 会**静默把 `\n` 转成 CRLF**
（`newline=None` 时的平台默认行为）。本会话用它改过一次 `scripts/migrate-server.sh`，
导致整文件行尾翻转、`git diff` 显示 519 插入 / 489 删除。
写 shell 脚本必须显式 `newline="\n"`，或改用 `write_bytes()`。

---

## 4. 第二层：真机（生产服务器）验证 —— **尚未执行**

WSL 证明不了的项，必须在真机上做（命令见手册 §4）。待办清单：

| # | 待验证项 | 手册位置 | 期望 |
| --- | --- | --- | --- |
| 1 | 服务器上脚本行尾无 CRLF、`bash -n` 通过 | §4.1 / §3 | 无 `CRLF:` 输出；"全部语法 OK" |
| 2 | `systemd-analyze verify` + `systemctl show` 生效值 | §4.2 | `UMask=0027`、`RuntimeDirectoryMode=0750`、`ReadWritePaths` 符合 X-13/X-23 预期 |
| 3 | `/run/gipfel/gipfel.sock` 不存在；端口只绑回环 | §4.2 | `No such file`；`127.0.0.1:8000` / `:8121` / `0.0.0.0:8120` |
| 4 | 部署日志中**不含**管理员口令明文 | §4.3 | `grep -qF "$PW" deploy.log` 无命中 |
| 5 | 部署退出码反映真实结果（X-24） | §4.3 | 无问题时 0；有检查未通过时非 0 |
| 6 | 两个功能探针（X-24） | §4.4 | logviewer 与 `/api/health` 均 2xx/3xx |
| 7 | 备份快照自洽（X-10） | §4.5 | `integrity_check = ok` 且表数量 > 0 |
| 8 | `migrate-server.sh --dry-run` 零网络零残留（X-21） | §4.6 | exit 0、全 `[DRY-RUN]`、无 `/tmp` 残留 |
| 9 | 非 22 端口 + 密钥 + known_hosts 的推送（X-25） | §4.6 | 出现"已用 -i 指定私钥"，不出现"未指定 --ssh-known-hosts" |
| 10 | 诊断脚本密钥掩码（X-14） | §4.7 | 只显示 `前4位****(len=N)` |
| 11 | 升级 + 回滚演练（X-10） | §4.8 | 失败时脚本自行打印六步回滚指引，照做后服务恢复 |

**为什么不能在 WSL 里做完**（WSL 缺失或语义不同）：

- 缺 `nginx` / `ufw` / `sqlite3`(CLI) / `node`：nginx `-t`/reload、ufw 放行、前端构建都无法验证；
- `systemd-analyze verify` 只校验**指令级正确性**，不能证明 `systemctl start` 的**运行时**行为
  （`/opt/gipfel` 下没有真实安装，ExecStart 的可执行文件不存在）；
- `/mnt/c` 是 DrvFs，权限位/大小写敏感性/IO 特性与真实 Linux 文件系统不同；
- 真实 SSH/rsync 传输、云安全组、HTTPS/DNS/浏览器行为都无法在本地复现。

---

## 5. 该结果对 §8 认证的影响

- §8 的 **X 系列静态/函数级结论** 现在有真实 Linux 背书（44/44），比此前"Git bash + 静态推演"强；
- §8 认证行里"真实 systemd / nginx / SSH 远端路径未在真机验证"这条**依然成立**，
  只是范围缩小了：`systemd-analyze verify` 这一层已补上，剩下的见 §4；
- 新增缺陷 **X-27** 已修复并单独提交（`179712f`），建议在审计报告 §8 的第九批清单里同步登记。

---

## 6. 附：本次用到的脚本

| 文件 | 用途 |
| --- | --- |
| `code_audit/_wsl_verify.sh` | 主验证脚本，44 项检查（内容与手册 §2.4 内嵌版本一致，已验证可独立运行） |
| `code_audit/_wsl_eol.sh` | 行尾诊断：对比工作区 / git 对象库的 CRLF-LF，并演示 Linux bash 对 CRLF 的拒收 |
| `code_audit/_WSL_verify_out.txt` | 主验证脚本的完整原始输出 |

> 说明：`code_audit/` 是**未跟踪**的审计与探针产物目录（与本记录、审计报告同样是本地文件）。
> 若希望这些内容也进版本库，需要单独说明。
