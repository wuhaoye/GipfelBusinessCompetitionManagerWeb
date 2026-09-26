# WSL 与生产环境验证操作手册

> **这份文档给谁用**：交给另一个对话（或另一个执行者）照做即可完成两件事 ——
> ① 用 WSL 里的真实 Linux 复验所有 bash/部署脚本；② 在真机上验证生产环境。
>
> **写这份文档时的仓库状态**：分支 `bugfix-merged`，HEAD `179712f`，`master` 未被改动（`97e117e`）。
>
> **验证状态总览**（详见《[WSL与生产环境验证结果记录.md](WSL与生产环境验证结果记录.md)》）：
>
> | 层 | 状态 | 结果 |
> | --- | --- | --- |
> | §2 WSL 真实 Linux（44 项） | ✅ **已实测** | `PASS=44 FAIL=0`（Ubuntu 26.04 / bash 5.3.9 / systemd 为 PID1） |
> | §3 行尾与传输前置检查 | ✅ 已实测并**修掉一个真实缺陷** | X-27 → 提交 `179712f`（`.gitattributes: *.sh text eol=lf`） |
> | §4 真机（生产服务器）验证 | ⬜ **尚未执行** | 11 项待办，见 §4 各小节前的 ⬜ 标记 |

---

## 0. 先看这三条"坑"

1. **不要用 Git for Windows 的 bash 验证脚本能否在 Linux 上跑。** 它容忍 CRLF，Linux 的 bash 不容忍。
   本仓库曾因此漏掉一个真实缺陷：Windows 工作区把 `.sh` 检出成 CRLF 时，8 个脚本在 Linux 上**全部**
   `bash -n` 失败（`$'\r': command not found`）。已用 `.gitattributes`（`*.sh text eol=lf`）修掉（提交 `179712f`）。
2. **WSL 里没有的东西**：`nginx`、`ufw`、`sqlite3` CLI、`node`（本机 `npm` 是 Windows 的 `/mnt/d/nodejs/npm`）、
   `pip3`/`python3-venv`。这些要么 `apt install`，要么只能上真机验证（§4）。
3. **仓库放在 `/mnt/c`**（DrvFs）：可读写，但权限位/大小写敏感性/IO 性能与真 Linux 文件系统不同。
   凡涉及 `chmod`/`chown`/systemd 的验证，建议先把代码 `git archive` 到 `/tmp` 再跑（§2.3 给了命令）。

---

## 1. 环境准备

### 1.1 本机现状（已确认）

| 项 | 值 |
| --- | --- |
| 发行版 | `Ubuntu-26.04`（WSL2，状态 Running） |
| 内核 | `6.18.33.2-microsoft-standard-WSL2` |
| PID 1 | `systemd`（`systemctl is-system-running` → `running`） |
| bash | GNU bash 5.3.9(1)-release (x86_64-pc-linux-gnu) |
| python3 | 3.14.4，`sqlite3` 模块 3.46.1 |
| 已有工具 | `rsync` `ssh` `curl` `git` `sudo` `ss` `systemctl` `systemd-analyze` |
| 缺失工具 | `nginx` `ufw` `sqlite3`(CLI) `node` `pip3` `python3-venv` |
| 仓库路径（WSL 视角） | `/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb` |

### 1.2 调用方式（从 Windows 侧发起）

```powershell
# 一条命令跑一个脚本（注意 WSL_UTF8=1，否则中文输出会乱码）
$env:WSL_UTF8='1'
wsl.exe -d Ubuntu-26.04 -- bash /mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb/code_audit/_wsl_verify.sh
```

> **给 AI 执行者的提示**：若用受限沙箱执行 `wsl.exe` 会报 `Wsl/EnumerateDistros E_ACCESSDENIED`
> 或 `Win32 error 5`。这不是"没装 WSL"，而是沙箱拦了命名管道；需要提权到 `danger-full-access`
> 才能启动 WSL。先跑 `wsl.exe -l -v` 确认能列出 `Ubuntu-26.04` 再往下做。

### 1.3 想覆盖更多项时，先装依赖（可选，需要网络）

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip sqlite3 rsync nginx ufw
# node 建议用 NodeSource 源（部署脚本 deploy-linux.sh 也是这么装的），或用 nvm
```

装完 `sqlite3` CLI 后，§3 的数据库校验可以换成更直观的 `sqlite3 db 'pragma integrity_check'`。

---

## 2. 第一层验证：WSL 能做的（真实 Linux）

### 2.1 一条命令跑完全套

```powershell
$env:WSL_UTF8='1'
wsl.exe -d Ubuntu-26.04 -- bash /mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb/code_audit/_wsl_verify.sh
```

期望结尾：`PASS=44 FAIL=0`（退出码 0）。

若该脚本不在（`code_audit/` 是未跟踪目录），用 §2.4 的完整脚本内容重建它。

### 2.2 它验证了什么（44 项明细，**全部已在本机 WSL 实测通过**）

| 段 | 内容 | 期望 | 实测 |
| --- | --- | --- | --- |
| 0 | 环境自述 | bash/python3/systemd 版本打印出来 | ✅ |
| 1 | 全部 8 个被跟踪 `.sh` 的 `bash -n` | 全部 OK（**必须是 LF**，见 §0 坑 1） | ✅ 8/8 |
| 2 | `tests/deploy_public_ip_test.sh` 在 `tests/`、仓库根、`/tmp` 三种 cwd 下 | 均 `PASS=36 FAIL=0` | ✅ 3/3 |
| 3 | `deploy-common.sh` 的 `absolutize_dir` / `log_viewer_port` | `/tmp/a/c`、`/opt/gipfel`、`9000`、非法回退 `8120` | ✅ 4/4 |
| 4 | `snapshot_sqlite_consistent` 对**活的 WAL 库**取快照 | 成功且 `integrity=ok`；对照的 `cp -a` 副本打不开 | ✅（`rows=3000`；对照 `no such table: t`） |
| 5 | `migrate-server.sh` 退出码 + `--dry-run` | `--help`=0 / 未知参数=2 / 缺 `--mode`=1 / 非法端口=2；dry-run 退 0、无真实连接、无 `/tmp/gipfel-migration-*` 残留 | ✅ 8/8 |
| 6 | `quick-sync.sh` | 不存在的推送源被拒（rc=1）；SSH 选项被识别；给了 known_hosts 不再告警 accept-new；非法端口 rc=2 | ✅ 4/4 |
| 7 | `deploy-linux.sh` | 帮助含 `--print-seed-password` 与 `--allow-partial`；口令守卫 2 处；收尾无"无条件 ok" | ✅ 4/4 |
| 8 | `update-from-github.sh` / `verify-migration.sh` | `--help` 非崩溃退出；计数器不再用 `((PASS++))` | ✅ 3/3 |
| 9 | **`systemd-analyze verify`**（真 systemd） | 无指令级错误；`gipfel.service` 无 `-u …gipfel.sock`；`RuntimeDirectoryMode=0750`；`UMask=0027` | ✅ |
| 10 | `print_rollback_hint` | 六步命令齐全（停服务/恢复库/恢复上传配置/回退代码/重装依赖前端/起服务健康检查） | ✅ 5/5 |

> 原始输出存档：`code_audit/_WSL_verify_out.txt`。

### 2.3 在真 Linux 文件系统上再跑一遍（可选，更接近生产）

```bash
REPO=/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb
rm -rf /tmp/gipfel-repo && mkdir -p /tmp/gipfel-repo
git -C "$REPO" archive HEAD | tar -x -C /tmp/gipfel-repo          # 只含被跟踪文件，LF 行尾
cd /tmp/gipfel-repo
bash -n $(git -C "$REPO" ls-files '*.sh') && echo "语法 OK"
REPO=/tmp/gipfel-repo bash "$REPO/code_audit/_wsl_verify.sh"      # 脚本需一并存在
```

> 注：`git archive` 出来的树**不含** `code_audit/`（未跟踪），所以要么把验证脚本单独拷进去，
> 要么先 `cp -r "$REPO/code_audit" /tmp/gipfel-repo/`。

### 2.4 验证脚本完整内容（`code_audit/_wsl_verify.sh`，可原样重建）

```bash
#!/usr/bin/env bash
# 生产相关 bash 内容的 WSL(Ubuntu) 复验脚本
# 用法：wsl -d Ubuntu-26.04 -- bash code_audit/_wsl_verify.sh
# 退出码：0 = 全部通过；非 0 = 有失败项
set -uo pipefail

REPO="${REPO:-/mnt/c/Users/wuhao/Desktop/shang/gipfel/GipfelBusinessCompetitionManagerWeb}"
WORK="$(mktemp -d /tmp/gipfel-wslverify-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m %s\n' "$*"; }
chk()  { local d="$1" got="$2" exp="$3"; [[ "$got" == "$exp" ]] && ok "$d（=$got）" || bad "$d（got=$got exp=$exp）"; }
sec()  { printf '\n\033[36m== %s ==\033[0m\n' "$*"; }

cd "$REPO" || { echo "找不到仓库 $REPO"; exit 2; }

sec "0. 环境自述"
printf '  %s\n' "$(bash --version | head -1)"
printf '  %s / init=%s / systemd=%s\n' "$(python3 -V 2>&1)" "$(ps -p 1 -o comm=)" "$(systemctl is-system-running 2>&1)"
printf '  python3 sqlite3=%s\n' "$(python3 -c 'import sqlite3;print(sqlite3.sqlite_version)' 2>&1)"

sec "1. 全部 shell 脚本语法（真 Linux bash）"
for f in $(git ls-files '*.sh'); do
    bash -n "$f" 2>/dev/null && ok "bash -n $f" || bad "bash -n $f"
done

sec "2. tests/deploy_public_ip_test.sh（三种 cwd）"
for d in "$REPO/tests" "$REPO" /tmp; do
    out="$(cd "$d" && bash "$REPO/tests/deploy_public_ip_test.sh" 2>&1)"
    line="$(printf '%s' "$out" | grep -E '^结果：' | tail -1)"
    if printf '%s' "$line" | grep -q 'FAIL=0'; then ok "cwd=$d → $line"; else bad "cwd=$d → $line"; fi
done

sec "3. scripts/lib/deploy-common.sh 纯函数"
# shellcheck source=scripts/lib/deploy-common.sh
source scripts/lib/deploy-common.sh
chk "absolutize_dir ./a/b/../c" "$(cd /tmp && absolutize_dir ./a/b/../c)" "/tmp/a/c"
chk "absolutize_dir /opt/gipfel//x/.." "$(absolutize_dir /opt/gipfel//x/..)" "/opt/gipfel"
printf 'LOG_VIEWER_PORT=9000\n' > "$WORK/env.9000"
printf 'LOG_VIEWER_PORT=abc\n'  > "$WORK/env.bad"
chk "log_viewer_port 读取 9000" "$(log_viewer_port "$WORK/env.9000")" "9000"
chk "log_viewer_port 非法回退"  "$(log_viewer_port "$WORK/env.bad")" "8120"

sec "4. snapshot_sqlite_consistent（真实 python3 + WAL 活库）"
python3 - "$WORK/live.sqlite3" <<'PY' &
import sqlite3, sys, time
con = sqlite3.connect(sys.argv[1], timeout=30)
con.execute("pragma journal_mode=wal")
con.execute("create table t(id integer primary key, v text)")
for b in range(15):
    con.executemany("insert into t(v) values(?)", [(f"r{b}-{i}" * 20,) for i in range(500)])
    con.commit(); time.sleep(0.3)
con.close()
PY
WRITER=$!
sleep 1.5
cp -a "$WORK/live.sqlite3" "$WORK/plain.sqlite3"
if snapshot_sqlite_consistent "$WORK/live.sqlite3" "$WORK/snap.sqlite3"; then ok "snapshot_sqlite_consistent 成功"; else bad "snapshot_sqlite_consistent 失败"; fi
kill "$WRITER" 2>/dev/null; wait "$WRITER" 2>/dev/null
python3 - "$WORK" <<'PY'
import sqlite3, sys, pathlib
work = pathlib.Path(sys.argv[1])
for name in ("plain.sqlite3", "snap.sqlite3"):
    p = work / name
    if not p.exists():
        print(f"  INFO {name}: 不存在"); continue
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            print(f"  INFO {name}: integrity={con.execute('pragma integrity_check').fetchone()[0]} rows={con.execute('select count(*) from t').fetchone()[0]}")
        finally:
            con.close()
    except sqlite3.Error as exc:
        print(f"  INFO {name}: 打不开/损坏 -> {exc}")
PY

sec "5. scripts/migrate-server.sh 退出码与 --dry-run"
run_rc() { ( cd "$WORK" && bash "$REPO/scripts/migrate-server.sh" "$@" >/dev/null 2>&1 ); echo $?; }
chk "--help"                    "$(run_rc --help)" "0"
chk "未知参数"                   "$(run_rc --install_dir /opt/gipfel)" "2"
chk "缺 --mode"                 "$(run_rc --target u@127.0.0.1)" "1"
chk "--ssh-port abc"            "$(run_rc --mode push --target u@127.0.0.1 --install-dir /tmp --ssh-port abc)" "2"
before="$(ls -d /tmp/gipfel-migration-* 2>/dev/null | wc -l)"
out="$( cd "$WORK" && bash "$REPO/scripts/migrate-server.sh" --dry-run --mode pull --source u@127.0.0.1 --install-dir /tmp 2>&1 )"; rc=$?
after="$(ls -d /tmp/gipfel-migration-* 2>/dev/null | wc -l)"
chk "--dry-run --mode pull 退出码" "$rc" "0"
printf '%s' "$out" | grep -q 'DRY-RUN' && ok "--dry-run 打印了预演计划" || bad "--dry-run 没有预演输出"
printf '%s' "$out" | grep -q '无法连接到源服务器' && bad "--dry-run 仍发起了真实连接" || ok "--dry-run 未发起真实连接"
chk "--dry-run 无临时目录残留" "$before" "$after"

sec "6. scripts/quick-sync.sh（相对 INSTALL_DIR / push 源校验 / SSH 选项）"
mkdir -p "$WORK/empty-install/backend"
q() { ( cd "$WORK" && bash "$REPO/scripts/quick-sync.sh" "$@" 2>&1 ); echo "RC=$?"; }
o1="$(q push user@nonexistent ./no-such-dir)"
printf '%s' "$o1" | grep -q '推送源不可用' && printf '%s' "$o1" | grep -q 'RC=1' && ok "不存在的推送源被拒绝" || bad "不存在的推送源未被拒绝：$(printf '%s' "$o1" | tail -2 | tr '\n' ' ')"
o2="$(q push user@nonexistent empty-install --ssh-port 2222 --ssh-key /tmp/k --ssh-known-hosts /tmp/kh)"
printf '%s' "$o2" | grep -q '已用 -i 指定私钥' && ok "SSH 选项被识别" || bad "SSH 选项未被识别"
printf '%s' "$o2" | grep -q '未指定 --ssh-known-hosts' && bad "给了 known_hosts 仍告警 accept-new" || ok "给了 known_hosts 不再告警 accept-new"
chk "非法 --ssh-port" "$(printf '%s' "$(q push user@nonexistent empty-install --ssh-port abc)" | tail -1)" "RC=2"

sec "7. scripts/deploy-linux.sh（帮助 / 口令默认不打印 / 退出码语义）"
out="$(bash scripts/deploy-linux.sh --help 2>&1)"
printf '%s' "$out" | grep -q -- '--print-seed-password' && ok "帮助里有 --print-seed-password" || bad "帮助缺 --print-seed-password"
printf '%s' "$out" | grep -q -- '--allow-partial' && ok "帮助里有 --allow-partial" || bad "帮助缺 --allow-partial"
n_guard="$(grep -c 'PRINT_SEED_PASSWORD" == "1" && -t 1' scripts/deploy-linux.sh)"
chk "TTY+显式开关守卫处数" "$n_guard" "2"
chk "收尾无条件 ok 行数" "$(grep -c '^ok "部署完成！"$' scripts/deploy-linux.sh)" "0"

sec "8. scripts/update-from-github.sh 与 verify-migration.sh"
out="$(bash scripts/update-from-github.sh --help 2>&1)"; rc=$?
[[ $rc -eq 0 || $rc -eq 1 || $rc -eq 2 ]] && ok "update-from-github --help 以 $rc 结束（非崩溃）" || bad "update-from-github --help rc=$rc"
out="$(bash scripts/verify-migration.sh --help 2>&1)"; rc=$?
[[ $rc -eq 0 || $rc -eq 1 || $rc -eq 2 ]] && ok "verify-migration --help 以 $rc 结束（非崩溃）" || bad "verify-migration --help rc=$rc"
grep -q 'PASS=$((PASS + 1))' scripts/verify-migration.sh && ok "verify-migration 计数不再用 ((PASS++))" || bad "verify-migration 计数器仍是 ((PASS++))"

sec "9. systemd 单元（真 systemd-analyze verify）"
mkdir -p "$WORK/units"
for u in deploy/gipfel.service deploy/logviewer.service; do
    b="$(basename "$u")"
    sed -e 's|__INSTALL_DIR__|/opt/gipfel|g' "$u" > "$WORK/units/$b"
done
if command -v systemd-analyze >/dev/null 2>&1; then
    # 只关心**指令级**错误；"Command … is not executable / could not be found" 是因为
    # /opt/gipfel 下没有真实安装，属于预期噪音，过滤掉。
    verr="$(systemd-analyze verify "$WORK/units/gipfel.service" "$WORK/units/logviewer.service" 2>&1 \
            | grep -viE 'is not executable|could not be found|not found|does not exist|Failed to (prepare|load)|Cannot (find|load)' || true)"
    if [[ -z "$verr" ]]; then ok "systemd-analyze verify 无指令级错误"; else bad "systemd-analyze verify：$verr"; fi
else
    bad "缺 systemd-analyze"
fi
grep -q -- '-u /run/gipfel/gipfel.sock' deploy/gipfel.service && bad "gipfel.service 仍在监听 Unix socket" || ok "gipfel.service 已移除 Unix socket"
grep -q 'RuntimeDirectoryMode=0750' deploy/gipfel.service && ok "gipfel.service 运行目录 0750" || bad "gipfel.service 运行目录未收紧"
grep -q 'UMask=0027' deploy/logviewer.service && ok "logviewer.service UMask=0027" || bad "logviewer.service 缺 UMask"

sec "10. print_rollback_hint 输出"
out="$(print_rollback_hint /opt/gipfel "$WORK/snap.sqlite3" "$WORK" abc1234 2>&1)"
for n in 'systemctl stop gipfel gipfel-logviewer' "checkout 'abc1234'" 'npm ci' 'api/health' '只含数据'; do
    printf '%s' "$out" | grep -qF -- "$n" && ok "回滚指引含：$n" || bad "回滚指引缺：$n"
done

printf '\n\033[36m════ 汇总 ════\033[0m\n'
printf '  PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
[[ $FAIL -eq 0 ]] && exit 0 || exit 1
```

### 2.5 Python 侧回归也可以在 WSL 里跑（可选）

WSL 的 `python3` 缺 `venv`/`pip`，要先 `sudo apt-get install -y python3-venv python3-pip`，然后：

```bash
cd /tmp/gipfel-repo/backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python manage.py test apps                 # 期望 Ran 153 tests ... OK
python manage.py test tests_fix_verify     # 期望 Ran 280 tests ... OK
cd .. && python tests/big_number_smoke.py  # 期望 PASS=22 FAIL=0
python tests/sqlite_decimal_roundtrip.py   # 期望 PASS=10 FAIL=0（且不碰 backend/db.sqlite3）
```

---

## 3. 行尾与"传输"前置检查（每次交付到服务器前都做）  ✅ 已实测 —— 本项曾查出真实缺陷 X-27

> **本节的检查不是走过场**：用 WSL 复验时，本节对应的检查发现 Windows 工作区的 8 个 `.sh`
> 全是 CRLF、在 Linux 上 `bash -n` **8/8 失败**（Git for Windows 的 bash 却容忍，因此此前一直没暴露）。
> 已通过提交 `179712f` 新增 `.gitattributes`（`*.sh text eol=lf`）修复。
> 完整记录见《[WSL与生产环境验证结果记录.md](WSL与生产环境验证结果记录.md)》§3。

```bash
# 在服务器上（或 WSL 里对将要上传的目录）执行：
cd <安装目录>
for f in $(git ls-files '*.sh' 2>/dev/null || find . -name '*.sh'); do
    if grep -q $'\r' "$f"; then echo "CRLF!! $f"; fi
done
# 或一次性修掉：
find . -name '*.sh' -exec sed -i 's/\r$//' {} +

bash -n scripts/deploy-linux.sh && echo "语法 OK"
```

**为什么必须查**：Linux 的 bash 不接受 CRLF，报错长这样，很容易被误判成"脚本写错了"：

```
scripts/deploy-linux.sh: line 16: $'\r': command not found
scripts/lib/deploy-common.sh: line 26: syntax error near unexpected token `$'{\r''
```

若走 `deploy/README.md` 里"服务器连不上 GitHub 就打 tar 包传上去"的流程，**打包前务必确认包内 `.sh` 是 LF**。

---

## 4. 第二层验证：必须上真机（WSL 覆盖不到）

> 目标机器：计划/已在跑 `gipfel` 的那台 Linux 服务器。以下命令多数要 `sudo`。
> **不要**在生产机上做破坏性操作（不要真跑 `migrate` 失败路径）；只做只读/可逆的检查。

### 4.1 部署前：脚本与依赖自检  ⬜ 未实测（待真机）

```bash
cd /opt/gipfel 2>/dev/null || cd /opt/GipfelBusinessCompetitionManagerWeb

# 1) 行尾（见 §3）
for f in scripts/*.sh scripts/lib/*.sh tests/*.sh; do grep -q $'\r' "$f" && echo "CRLF: $f"; done

# 2) 语法
bash -n scripts/*.sh && echo "全部语法 OK"

# 3) 脚本能力自检（不会改任何东西）
bash scripts/deploy-linux.sh --help | grep -E -- '--print-seed-password|--allow-partial'
bash scripts/migrate-server.sh --help | grep -E -- '--ssh-known-hosts|--dry-run'
bash scripts/update-from-github.sh --help | head -20

# 4) 依赖
for c in python3 rsync ssh curl nginx systemctl ufw ss git; do printf '%-10s %s\n' "$c" "$(command -v $c || echo MISSING)"; done
/opt/gipfel/backend/.venv/bin/python -V
```

期望：第 1 步无输出；第 2 步打印"全部语法 OK"；第 3 步能看到新开关；依赖里除 `ufw`（可缺）外都在。

### 4.2 systemd 单元（X-13 / X-23）  ⬜ 未实测（待真机）

```bash
# 单元语法（真实 systemd）
sudo systemd-analyze verify /etc/systemd/system/gipfel.service /etc/systemd/system/logviewer.service
echo "verify exit=$?"          # 期望 0；"Command ... is not executable" 以外的输出才需要关注

# 生效值
systemctl show gipfel -p UMask -p RuntimeDirectory -p RuntimeDirectoryMode -p LogsDirectoryMode -p ReadWritePaths
systemctl show gipfel-logviewer -p UMask -p RuntimeDirectory -p RuntimeDirectoryMode -p ReadWritePaths

# 期望：
#   gipfel:            UMask=0027 RuntimeDirectory=gipfel RuntimeDirectoryMode=0750
#                      ReadWritePaths=…/uploads …/logs …/db.sqlite3 /run/gipfel /var/log/gipfel
#   gipfel-logviewer:  UMask=0027 RuntimeDirectory=gipfel-logviewer RuntimeDirectoryMode=0750
#                      ReadWritePaths=/run/gipfel-logviewer /var/log/gipfel-logviewer
#                      （**不应**出现 db.sqlite3 / backend/uploads / /run/gipfel）

# 权限
ls -ld /run/gipfel /var/log/gipfel /run/gipfel-logviewer /var/log/gipfel-logviewer
#   期望 drwxr-x--- （0750）

# 关键：不应再有 Unix socket（X-23 已移除）
ls -l /run/gipfel/gipfel.sock 2>&1        # 期望 No such file or directory

# 端口只绑回环，nginx 走 127.0.0.1:8000
sudo ss -ltnp | grep -E ':8000|:8121|:8120'
#   期望：127.0.0.1:8000 (daphne)、127.0.0.1:8121 (daphne-logviewer)、0.0.0.0:8120 (nginx)
```

### 4.3 部署脚本行为（X-22 / X-24）  ⬜ 未实测（待真机）

```bash
# 在**测试机**或维护窗口内执行；观察日志里是否出现管理员口令
sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx 2>&1 | tee /tmp/deploy.log
echo "deploy exit=$?"      # 期望 0；若有检查未通过则非 0（这正是 X-24 的修复）

# X-22：日志里不得出现口令明文
PW=$(sudo grep -E '^SEED_ADMIN_PASSWORD=' /opt/gipfel/backend/.env | cut -d= -f2-)
grep -qF "$PW" /tmp/deploy.log && echo "!! 口令泄露到日志" || echo "OK：日志里没有口令"

# 需要在本终端看口令时才加 --print-seed-password（且必须是 TTY）
sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx --print-seed-password
#   管道/CI 下会明确告警"stdout 不是终端…此处不打印"
```

### 4.4 功能探针（X-24 新增的两个）  ⬜ 未实测（待真机）

```bash
LV_PORT=$(grep -E '^LOG_VIEWER_PORT=' /opt/gipfel/backend/.env | cut -d= -f2- | tr -d ' \r')
LV_PORT=${LV_PORT:-8120}
curl -s -o /dev/null -w "logviewer http=%{http_code}\n" --max-time 5 "http://127.0.0.1:${LV_PORT}/"
curl -s -o /dev/null -w "api/health http=%{http_code}\n" --max-time 5 http://127.0.0.1/api/health
#   期望均为 2xx/3xx
```

### 4.5 数据库与回滚副本（X-10）  ⬜ 未实测（待真机）

```bash
DB=/opt/gipfel/backend/db.sqlite3
ls -l "$DB" /opt/gipfel/_backup/ | tail -5

# 最近一次备份里的快照必须自洽
LATEST=$(ls -dt /opt/gipfel/_backup/*/ | head -1)
/opt/gipfel/backend/.venv/bin/python - "$LATEST/db.sqlite3" <<'PY'
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print("integrity_check =", con.execute("pragma integrity_check").fetchone()[0])
print("表数量 =", len(con.execute("select name from sqlite_master where type='table'").fetchall()))
con.close()
PY
#   期望 integrity_check = ok 且表数量 > 0；若打不开/表为 0，说明备份是活库裸 cp 的产物，不可用
```

### 4.6 SSH / rsync 路径（X-20 / X-21 / X-25）  ⬜ 未实测（待真机）

```bash
# 迁移脚本：先预演（零网络、零副作用）
bash scripts/migrate-server.sh --dry-run --mode pull --source user@old-server --install-dir /opt/gipfel
echo "exit=$?"                       # 期望 0；输出里应全是 [DRY-RUN]
ls -d /tmp/gipfel-migration-* 2>/dev/null && echo "!! 有残留" || echo "OK：无残留"

# 退出码语义
bash scripts/migrate-server.sh --help >/dev/null;                     echo "help=$?"        # 0
bash scripts/migrate-server.sh --install_dir /opt/gipfel >/dev/null;  echo "错参数=$?"      # 2
bash scripts/migrate-server.sh --target u@h >/dev/null;               echo "缺mode=$?"      # 1

# 非 22 端口 + 指定密钥/known_hosts 的推送（quick-sync）
bash scripts/quick-sync.sh push user@new-server /opt/gipfel \
     --ssh-port 2222 --ssh-key ~/.ssh/id_ed25519 --ssh-known-hosts ~/.ssh/known_hosts
#   观察：出现"安装目录已规范化为绝对路径"（若传了相对路径）、"已用 -i 指定私钥…"提示；
#   不应出现"未指定 --ssh-known-hosts"
```

### 4.7 日志查看器诊断脚本（X-14）  ⬜ 未实测（待真机）

```bash
bash tests/gipfel-logviewer-diag.sh /opt/gipfel 2>&1 | tee /tmp/diag.log
#   期望：LOGVIEWER_SECRET_KEY 显示成前 4 位 + ****(len=N)，绝不出现原文
PW=$(sudo grep -E '^LOGVIEWER_SECRET_KEY=' /opt/gipfel/backend/.env | cut -d= -f2-)
grep -qF "$PW" /tmp/diag.log && echo "!! 密钥泄露" || echo "OK：密钥已掩码"
```

### 4.8 升级与回滚演练（建议在测试机上做一次）  ⬜ 未实测（待真机）

```bash
# 1) 记录回滚点
sudo git -C /opt/GipfelBusinessCompetitionManagerWeb rev-parse HEAD | tee /tmp/head_before.txt
sudo -u gipfel cp -a /opt/gipfel/backend/db.sqlite3 /tmp/db.before

# 2) 跑升级
sudo bash scripts/update-from-github.sh --install-dir /opt/GipfelBusinessCompetitionManagerWeb
echo "update exit=$?"

# 3) 若中途失败，脚本会自己打印"回滚指引（照抄即可）"六步；照做一遍并确认服务恢复：
sudo systemctl start gipfel gipfel-logviewer
curl -fsS --max-time 5 http://127.0.0.1/api/health && echo " 后端 OK"
systemctl is-active gipfel gipfel-logviewer
```

---

## 5. 结果记录模板（交给上游时照这个格式回）

```
环境：Ubuntu <版本> / bash <版本> / systemd <PID1 是否> / 服务器：<内网IP或主机名>
仓库：分支 bugfix-merged，HEAD <commit>

第一层（WSL 真实 Linux）
  [ ] bash -n 8/8 OK
  [ ] tests/deploy_public_ip_test.sh 三种 cwd 均 PASS=36 FAIL=0
  [ ] code_audit/_wsl_verify.sh → PASS=__ FAIL=__
  [ ] （可选）manage.py test apps / tests_fix_verify → __ / __

第二层（真机）
  [ ] 4.1 脚本自检：行尾无 CRLF；语法 OK；新开关可见
  [ ] 4.2 systemd：UMask=0027、RunTimeDirectoryMode=0750、无 gipfel.sock、端口只绑回环
  [ ] 4.3 部署：退出码 __；日志中无管理员口令明文
  [ ] 4.4 探针：logviewer http=___，api/health http=___
  [ ] 4.5 备份：integrity_check=ok，表数量=__
  [ ] 4.6 SSH：dry-run exit=0 且无残留；--help/错参数/缺mode = 0/2/1
  [ ] 4.7 诊断脚本：密钥已掩码
  [ ] 4.8 升级+回滚演练：成功 / 失败（附输出）

异常与原始输出：
  <贴命令 + 完整输出>
```

---

## 6. 本机已跑过的实测记录（可核对）  ✅ 已实测

> 本节是同一次运行的摘要；**完整版（含环境、逐项状态表、缺陷 X-27 的完整取证、待办清单）**
> 见《[WSL与生产环境验证结果记录.md](WSL与生产环境验证结果记录.md)》。原始输出：`code_audit/_WSL_verify_out.txt`。

**第一层（WSL Ubuntu 26.04 / GNU bash 5.3.9 / systemd 为 PID1）**

```
== 1. 全部 shell 脚本语法（真 Linux bash） ==   8/8 PASS
== 2. tests/deploy_public_ip_test.sh ==         三种 cwd 均 PASS=36 FAIL=0
== 3. deploy-common.sh 纯函数 ==                4/4 PASS
== 4. snapshot_sqlite_consistent ==             PASS（对照：cp -a 活库 → 打不开/损坏 no such table: t）
                                                snap.sqlite3: integrity=ok rows=3000
== 5. migrate-server.sh ==                      --help=0 未知参数=2 缺mode=1 端口非法=2
                                                --dry-run 退出0、有预演输出、未真实连接、无残留
== 6. quick-sync.sh ==                          4/4 PASS
== 7. deploy-linux.sh ==                        4/4 PASS
== 8. update-from-github / verify-migration ==  3/3 PASS
== 9. systemd-analyze verify ==                 无指令级错误；无 gipfel.sock；0750；UMask=0027
== 10. print_rollback_hint ==                   5/5 PASS
════ 汇总 ════  PASS=44 FAIL=0
```

**顺带发现并修复的真实缺陷**（提交 `179712f`）：Windows 工作区把 `.sh` 检出成 CRLF → Linux bash 下
8/8 脚本 `bash -n` 失败；LF 副本 8/8 OK；git 对象库本来就是 LF。已加 `.gitattributes`（`*.sh text eol=lf`）固定下来。
**同时更正一条口径**：此前报告的"8 个脚本 `bash -n` 全部通过"是在 **Git for Windows 的 bash** 下跑的，
它容忍 CRLF，因此那条结论不能证明 Linux 可用。

**第二层（真机）**：尚未执行 —— 本文档 §4 就是待办清单。

---

## 7. 边界说明（别把 WSL 的结论当万能的）

| WSL 能证明 | WSL **不能**证明 |
| --- | --- |
| 脚本语法、函数逻辑、退出码、参数解析 | nginx 配置能否 `-t` 通过、能否 reload 成功 |
| `systemd-analyze verify` 的指令级正确性 | `systemctl start/restart` 的**运行时**行为（需真实安装） |
| `snapshot_sqlite_consistent` 在真 SQLite 上的自洽性 | 真实生产库的规模/锁竞争/磁盘压力 |
| `migrate-server.sh --dry-run` 零网络零副作用 | 真实 SSH/rsync 传输、非 22 端口、known_hosts 固定 |
| 端口/防火墙**配置文本**的一致性 | `ufw` 规则是否真的放行、云安全组是否开放 |
| 行尾、编码、路径解析 | HTTPS 证书、DNS、浏览器端行为 |
