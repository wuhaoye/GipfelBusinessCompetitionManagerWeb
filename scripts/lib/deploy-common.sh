#!/usr/bin/env bash
# ============================================================
# 部署脚本公共函数（被 deploy-linux.sh 与 update-from-github.sh 共同 source）
#
# 审计 X-08：日志查看器公网端口（LOG_VIEWER_PORT）原先在 deploy-linux.sh 里正确解析、
# 而在 update-from-github.sh 里被**硬编码成 8120**（`LOG_VIEWER_PUBLIC_URL` 与 `ufw allow`）。
# 于是把 .env 改成别的端口后，升级路径会把 URL/防火墙规则改回 8120，而 nginx 仍监听新端口：
# 前端按钮跳向错误端口、运维按提示查防火墙也会被误导。这里把解析逻辑抽成**唯一实现**，
# 两个脚本共用，避免再次漂移。
#
# 用法（在脚本里）：
#   _common="$(dirname "$0")/lib/deploy-common.sh"   # 或脚本自带路径
#   # shellcheck source=scripts/lib/deploy-common.sh
#   [[ -f "$_common" ]] && source "$_common"
# ============================================================

# 日志前缀（调用方可以覆盖 log/warn/err；这里只提供兜底，避免 source 顺序问题）
command -v log  >/dev/null 2>&1 || log()  { echo "[deploy] $*"; }
command -v warn >/dev/null 2>&1 || warn() { echo "[deploy][warn] $*" >&2; }
command -v err  >/dev/null 2>&1 || err()  { echo "[deploy][error] $*" >&2; exit 1; }

# 解析日志查看器 nginx 公网监听端口：取 .env 的 LOG_VIEWER_PORT（默认 8120），
# 缺失/非数字/越界（1-65535）一律兜底 8120 并告警。
# 该端口是 nginx 监听 0.0.0.0:<port>，与 daphne 内部 127.0.0.1:8121 是两个端口
# （前者 .env 控制、后者 service 模板硬编码），故意不等，避免同机抢端口。
log_viewer_port() {
    local _env="${1:-}"
    local _p="8120"
    if [[ -n "$_env" && -f "$_env" ]] && grep -qE '^[[:space:]]*LOG_VIEWER_PORT=' "$_env"; then
        _p=$(grep -E '^[[:space:]]*LOG_VIEWER_PORT=' "$_env" | head -1 | cut -d= -f2- \
            | tr -d '[:space:]' | sed -E "s/^['\"]//; s/['\"]$//")
        if ! [[ "$_p" =~ ^[0-9]+$ ]] || (( _p < 1 || _p > 65535 )); then
            warn "LOG_VIEWER_PORT=$_p 非法（需 1-65535 整数），回退默认 8120"
            _p="8120"
        fi
    fi
    printf '%s' "$_p"
}

# 兼容旧调用名（deploy-linux.sh 历史函数名）
_log_viewer_port() { log_viewer_port "${1:-}"; }

# 确保 `.env` 中某个 KEY 只有一条记录且值恰好为 want（审计 X-11）。
#
# 改前两个部署脚本用 `sed -i "s|^KEY=.*|&,${AH_ENTRY}|"` 追加：`&` 代表**整个匹配文本**，
# 探测到的公网 IP 变化时会反复追加（历史 IP 永久留在 DJANGO_ALLOWED_HOSTS 白名单里），
# 且同一 KEY 出现多行时 `sed` 会同时改写多行，而 `os.environ` 只认**第一条** —— 脚本输出与
# 实际生效值不一致，排查极具误导性。
# 现在的做法：先删掉全部同名行，再追加唯一一行；改完立刻回读断言只剩一条。
# 用法：set_env_single <env文件> <KEY> <值>
set_env_single() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    [[ -n "$env_file" && -n "$key" ]] || return 1
    touch "$env_file"
    local tmp="${env_file}.tmp.$$"
    if ! grep -vE "^[[:space:]]*${key}=" "$env_file" > "$tmp" 2>/dev/null; then
        : > "$tmp"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
    mv -f "$tmp" "$env_file"
    # 回读断言：该 KEY 只能有一条
    local n
    n=$(grep -cE "^[[:space:]]*${key}=" "$env_file" 2>/dev/null || true)
    if [[ "${n:-0}" != "1" ]]; then
        warn "写入 ${key} 后回读发现 ${n:-0} 条记录（期望 1 条），请检查 ${env_file}"
        return 1
    fi
    return 0
}

# 把 entry 追加进逗号分隔的 KEY 值（去重），并保证该 KEY 只有一条记录（审计 X-11）。
# 用法：append_env_entry <env文件> <KEY> <entry> [默认值]
append_env_entry() {
    local env_file="$1"
    local key="$2"
    local entry="$3"
    local default_value="${4:-}"
    [[ -n "$entry" ]] || return 1
    local cur=""
    if [[ -f "$env_file" ]]; then
        cur=$(grep -E "^[[:space:]]*${key}=" "$env_file" | head -1 | cut -d= -f2- || true)
    fi
    [[ -n "$cur" ]] || cur="$default_value"
    # 逗号分隔去重（保留原有顺序）
    local out="" item
    local IFS=','
    for item in $cur; do
        item="${item//[[:space:]]/}"
        [[ -z "$item" ]] && continue
        [[ ",$out," == *",$item,"* ]] && continue
        out="${out:+$out,}$item"
    done
    if [[ ",$out," != *",$entry,"* ]]; then
        out="${out:+$out,}$entry"
    fi
    set_env_single "$env_file" "$key" "$out"
    printf '%s' "$out"
}

# 确保 .env 里的 LOG_VIEWER_PUBLIC_URL 与给定端口一致（审计 X-08）。
# 用法：ensure_log_viewer_public_url <env文件> <公网IP> <端口>
ensure_log_viewer_public_url() {
    local env_file="$1"
    local ip="$2"
    local port="$3"
    [[ -n "$env_file" && -n "$ip" && -n "$port" ]] || return 1
    # IPv6 需要方括号
    local host="$ip"
    if [[ "$ip" == *:* && "$ip" != \[* ]]; then
        host="[$ip]"
    fi
    local want="http://${host}:${port}/"
    # 审计 X-11：用「删全部同名行 + 追加唯一一行」替代 sed 整行替换（避免多行同时被改）
    set_env_single "$env_file" "LOG_VIEWER_PUBLIC_URL" "$want"
    printf '%s' "LOG_VIEWER_PUBLIC_URL=${want}"
}

# 自检：.env 的 LOG_VIEWER_PUBLIC_URL 端口 与 nginx vhost 的 listen 端口必须一致（审计 X-08）。
# 用法：assert_log_viewer_port_consistent <env文件> <vhost文件>
# 返回 0 一致 / 1 不一致（不一致时打印诊断）
assert_log_viewer_port_consistent() {
    local env_file="$1"
    local vhost="$2"
    [[ -f "$env_file" && -f "$vhost" ]] || return 0
    local url_port listen_port
    url_port=$(grep -E '^LOG_VIEWER_PUBLIC_URL=' "$env_file" | head -1 \
        | sed -E 's|.*:([0-9]+)/?.*|\1|')
    # vhost 里监听该端口的 server 块（取第一个 listen 的端口号集合即可）
    listen_port=$(grep -E '^[[:space:]]*listen[[:space:]]+[0-9]+' "$vhost" | head -1 \
        | sed -E 's|^[[:space:]]*listen[[:space:]]+([0-9]+).*|\1|')
    if [[ -n "$url_port" && -n "$listen_port" && "$url_port" != "$listen_port" ]]; then
        warn "端口不一致：.env 的 LOG_VIEWER_PUBLIC_URL 用 ${url_port}，nginx listen 用 ${listen_port}"
        return 1
    fi
    return 0
}

# 把路径规范成**绝对路径**（不要求目标已存在）。审计 X-20。
#
# 相对路径按**当前工作目录**（$PWD）解析 —— 与用户直觉一致，而不是按脚本所在目录：
# `bash scripts/quick-sync.sh push host ./opt/gipfel` 或在别的 cwd 下用相对路径调用时，
# 改前 `INSTALL_DIR` 原样透传，`rsync` 会把**另一个目录**里的同名文件当成数据源推给
# 生产机（直接覆盖目标机的 db.sqlite3 / .env），pull 方向也会在意外位置建目录。
#
# 优先用 `realpath -m`（coreutils）；不可用时退回纯 bash 实现（同样处理 `.` 与 `..`）。
# 用法：absolutize_dir <路径>   —— 结果经 stdout 返回，失败返回 1
absolutize_dir() {
    local p="${1:-}"
    [[ -n "$p" ]] || return 1
    if command -v realpath >/dev/null 2>&1; then
        local resolved=""
        if resolved=$(realpath -m -- "$p" 2>/dev/null) && [[ -n "$resolved" ]]; then
            printf '%s' "$resolved"
            return 0
        fi
    fi
    # 兜底：相对路径接到 $PWD 后逐段折叠
    [[ "$p" == /* ]] || p="$PWD/$p"
    local out="" seg
    local IFS='/'
    for seg in $p; do
        case "$seg" in
            ""|".") continue ;;
            "..")   out="${out%/*}" ;;
            *)      out="${out}/${seg}" ;;
        esac
    done
    printf '%s' "${out:-/}"
}

# 一致性快照一个 SQLite 库（审计 X-10）。
#
# 为什么不能用 `cp -a`：服务在跑时库可能处于 WAL 模式，`cp` 抓到的页与 WAL 不一致，
# 拿到的是一个"看起来正常、打开却可能损坏或缺最近事务"的文件 —— 而这是**唯一的回滚副本**。
# 这里用 SQLite 自己的 `VACUUM INTO`（只读打开、无需停服）导出一份自洽副本，并校验
# 文件头与 `pragma integrity_check`；没有 python3 时退回 `cp -a` 并明确告警。
#
# 用法：snapshot_sqlite_consistent <源库> <目标文件>   —— 成功返回 0
snapshot_sqlite_consistent() {
    local src="$1"
    local dst="$2"
    [[ -f "$src" ]] || return 1
    mkdir -p "$(dirname -- "$dst")" 2>/dev/null || true

    # 解释器可用性要**实际探测**：某些环境下 `python3` 只是 Windows Store 存根，
    # `command -v` 能找到但一执行就失败。可用 PYTHON_FOR_SNAPSHOT 覆盖（测试/venv 场景）。
    local py="${PYTHON_FOR_SNAPSHOT:-python3}"
    if ! "$py" -c 'import sqlite3' >/dev/null 2>&1; then
        py=""
    fi

    if [[ -n "$py" ]]; then
        if ! "$py" - "$src" "$dst" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    try:
        con.execute("VACUUM INTO ?", (dst,))
    finally:
        con.close()
except sqlite3.Error as exc:
    print(f"VACUUM INTO 失败: {exc}", file=sys.stderr)
    sys.exit(1)
PY
        then
            warn "一致性快照失败（VACUUM INTO）：$src"
            return 1
        fi
    else
        warn "找不到可用的 python3（可用 PYTHON_FOR_SNAPSHOT 指定），退回 cp -a —— 活库上可能得到不一致的副本"
        cp -a "$src" "$dst" || return 1
    fi

    [[ -s "$dst" ]] || { warn "快照为空：$dst"; return 1; }
    if [[ "$(head -c 15 "$dst" 2>/dev/null || true)" != "SQLite format 3" ]]; then
        warn "快照不是合法 SQLite 文件：$dst"
        return 1
    fi
    if [[ -n "$py" ]]; then
        if ! "$py" - "$dst" <<'PY'
import sqlite3, sys
try:
    con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    try:
        row = con.execute("pragma integrity_check").fetchone()
    finally:
        con.close()
except sqlite3.Error as exc:
    print(f"打开失败: {exc}", file=sys.stderr)
    sys.exit(1)
if not row or row[0] != "ok":
    print(f"integrity_check: {row}", file=sys.stderr)
    sys.exit(1)
PY
        then
            warn "快照完整性校验未通过：$dst"
            return 1
        fi
    fi
    return 0
}

# 部署/升级中途失败时，打印"怎么退回去"的确切命令（审计 X-10）。
#
# 背景：脚本在 `migrate` 之后还有 collectstatic / 前端构建 / 服务重启 / nginx 等步骤，
# 任何一步失败都会留下"新库结构 + 旧代码 + 服务停摆"的状态；而 `_backup/*` **只含数据
# 不含代码**，只恢复数据库会得到版本错配。这里把数据与代码两侧的命令一次说清。
#
# 用法：print_rollback_hint <安装目录> <数据库快照> <备份目录> [<升级前的 commit>]
print_rollback_hint() {
    local install_dir="$1"
    local db_snapshot="$2"
    local backup_dir="$3"
    local code_head="${4:-}"
    local _echo="warn"
    command -v warn >/dev/null 2>&1 || _echo="echo"

    $_echo "──────── 回滚指引（照抄即可） ────────"
    $_echo "本轮已在 migrate 之后失败：数据库结构可能是新的，而代码/服务未必配套。"
    $_echo "1) 停服务:        sudo systemctl stop gipfel gipfel-logviewer"
    if [[ -n "$db_snapshot" && -f "$db_snapshot" ]]; then
        $_echo "2) 恢复数据库:    sudo -u gipfel cp -a '$db_snapshot' '$install_dir/backend/db.sqlite3'"
    else
        $_echo "2) 恢复数据库:    未生成快照，请从 '$backup_dir' 里取 db.sqlite3"
    fi
    $_echo "3) 恢复上传/配置: sudo -u gipfel cp -a '$backup_dir/uploads/.' '$install_dir/backend/uploads/' 2>/dev/null; \\"
    $_echo "                  sudo -u gipfel cp -a '$backup_dir/.env' '$install_dir/backend/.env' 2>/dev/null; \\"
    $_echo "                  sudo chown gipfel:gipfel '$install_dir/backend/db.sqlite3' '$install_dir/backend/.env'; sudo chmod 600 '$install_dir/backend/.env'"
    if [[ -n "$code_head" ]]; then
        $_echo "4) 回退代码:      sudo git -C '$install_dir' checkout '$code_head'   # 本轮升级前的 commit"
    else
        $_echo "4) 回退代码:      sudo git -C '$install_dir' checkout <上一个可用 tag/commit>"
    fi
    $_echo "5) 重装依赖/前端: sudo -u gipfel '$install_dir/backend/.venv/bin/pip' install -r '$install_dir/backend/requirements.txt'; \\"
    $_echo "                  cd '$install_dir/frontend' && sudo npm ci && sudo npm run build"
    $_echo "6) 起服务确认:    sudo systemctl start gipfel gipfel-logviewer && curl -fsS --max-time 5 http://127.0.0.1/api/health"
    $_echo "备份目录（只含数据，不含代码）: $backup_dir"
    $_echo "─────────────────────────────────────"
    return 0
}

# 确保运行用户（默认 gipfel）与其同名组存在；幂等，失败即中止（审计 X-30）。
#
# 背景（真机事故：Debian 13 + `su` 非登录 shell 部署）：
#   ① 改前 deploy-linux.sh / update-from-github.sh 写的是
#      `if ! id gipfel; then useradd … || true; fi`。`useradd` 位于 /usr/sbin，而 `su`（不带 `-`）
#      的 PATH 是 /usr/local/bin:/usr/bin:/bin:/usr/games —— 不含 /usr/sbin，于是
#      `useradd: command not found`(127) 被 `|| true` 吞掉；脚本带着"没有运行用户"继续跑完
#      migrate + 前端构建，最后在 `chown -R gipfel:gipfel …` 处
#      （deploy-linux.sh 第 634 行）以 `chown: invalid user: 'gipfel:gipfel'` 终止 ——
#      报错完全指错方向（真正失败的是建用户，且发生在半小时之前）。同一 PATH 依赖还会让
#      第 869 行的 `nginx -t`（/usr/sbin/nginx）二次引爆。
#   ② 守卫 `id gipfel` 只查**用户**，而 `-U` 要求同名组未被占用：「组在、用户不在」时
#      `useradd -U` 退出 9（`useradd: group gipfel exists … use -g`），同样被吞掉。
#   ③ 该状态在真实运维中确实会出现：组内还有其它成员时 `userdel gipfel` **不会删组**
#      （`userdel: group gipfel not removed because it has other members`），或曾执行过
#      `groupadd gipfel`。
#
# 现在：用户与组一起校验；组已存在则用 `-g` 复用（不用 `-U`）；用户已在而组缺失则补建组；
# 任何一步失败都立即以可操作的信息中止，绝不再留给后面的 chown 去报错。
# 用法：ensure_runtime_user [用户名] [安装目录] [组名]
ensure_runtime_user() {
    local user="${1:-gipfel}"
    local dir="${2:-/opt/$user}"
    local group="${3:-$user}"
    local bin=""

    if id "$user" >/dev/null 2>&1 && getent group "$group" >/dev/null 2>&1; then
        return 0
    fi

    if ! id "$user" >/dev/null 2>&1; then
        bin="$(command -v useradd 2>/dev/null || true)"
        if [[ -z "$bin" || ! -x "$bin" ]]; then
            err "找不到 useradd（PATH=$PATH）：请确认已安装 passwd 包，并用 'su -' 或 sudo 运行本脚本（'su' 非登录 shell 的 PATH 不含 /usr/sbin）"
        fi
        if getent group "$group" >/dev/null 2>&1; then
            # 组已存在 → 复用该组（此时用 -U 会以 "group … exists" 失败，退出码 9）
            "$bin" -r -s /usr/sbin/nologin -g "$group" -d "$dir" "$user"
        else
            # 用户与组都不存在 → 一次建出（-U 建同名组）
            "$bin" -r -s /usr/sbin/nologin -U -d "$dir" "$user"
        fi
    fi

    if ! getent group "$group" >/dev/null 2>&1; then
        # 用户已在、组缺失 → useradd 会因"用户已存在"失败，必须单独补组
        bin="$(command -v groupadd 2>/dev/null || true)"
        if [[ -z "$bin" || ! -x "$bin" ]]; then
            err "找不到 groupadd（PATH=$PATH）：请确认已安装 passwd 包，并用 'su -' 或 sudo 运行本脚本"
        fi
        "$bin" -r "$group"
    fi

    if ! id "$user" >/dev/null 2>&1 || ! getent group "$group" >/dev/null 2>&1; then
        err "创建运行用户/组 $user 失败：请手工执行 'useradd -r -s /usr/sbin/nologin -U $user'（组已存在时用 -g $group）后重跑"
    fi
    log "运行用户/组 $user 已就绪"
}


