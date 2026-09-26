#!/usr/bin/env bash
# ============================================================
# Gipfel Business Competition Manager — Linux 一键部署脚本
# 适用：Ubuntu 22.04 / Debian 12，有 sudo 权限
# 用法：
#   sudo bash deploy-linux.sh --domain comp.example.com --install-dir /opt/gipfel
#   sudo bash deploy-linux.sh --install-dir /opt/gipfel --with-nginx --skip-install-deps
#
# 步骤：
#   1. 系统依赖（python3-venv python3-dev nodejs npm nginx rsync）
#   2. 同步代码到 $INSTALL_DIR，备份已有数据
#   3. 虚拟环境 + pip（含 C1-a 的 gunicorn / redis）
#   4. migrate（自动 seed 默认 admin）
#   5. npm ci + build → frontend-dist
#   6. systemd unit（C1-a 起是**三个**）：
#        gipfel.service        daphne(ASGI)  :8000  → /socket.io/（实时总线 hub）
#        gipfel-wsgi.service   gunicorn(WSGI):8002  → /api/、/admin/（多 worker 多线程）
#        gipfel-logviewer.service 日志查看器 :8121
#   7. [可选] nginx vhost 写入 + reload（upstream gipfel_django→8002、gipfel_socketio→8000，
#      并含 C3 的 limit_req/limit_conn 与 $request_time 日志格式）
# ============================================================
set -euo pipefail
# 强制标准输入来自 /dev/null：任何隐式 read/openssl 等待熵等都不会卡在终端等待输入。
# 需要交互的场景（手动填写公网 IP）已在脚本内用显式 read < /dev/stdin 处理。
exec 0</dev/null

# 审计 X-30：本脚本要用 /usr/sbin 下的命令（useradd、nginx），而 `su`（不带 `-`）的 PATH 是
# /usr/local/bin:/usr/bin:/bin:/usr/games —— 缺 /usr/sbin。真机事故：`useradd` 变成
# `command not found` 被后面的 `|| true` 吞掉，第二次现场是 `nginx -t` 直接 command not found。
# 这里显式补齐 sbin 路径，不再假设调用者的 PATH 恰好完整（apt-get/chown/systemctl 都在
# /usr/bin，所以环境"看起来正常"，非常容易漏判）。
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"

# ---------------- 失败时必须说清「停在哪一行」 ----------------
# 背景：`set -e` 失败时**不输出任何东西**，表现为脚本「跑一半就没了」，运维无法判断
# 停在哪一步、该手工补哪一步（升级脚本上已真实发生一次）。故装 ERR trap 打出定位信息。
# 触发条件与 set -e 一致（if/while 条件、&&/|| 列表、! 取反不触发）。
# 本 trap 不继承进函数体（未开 set -E，避免改变控制流的风险），覆盖顶层命令。
__on_err() {
    local rc="$1" line="$2" cmd="$3"
    {
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "[FAIL] 脚本在第 ${line} 行终止（退出码 ${rc}）"
        echo "       命令：${cmd}"
        echo ""
        echo "       已完成步骤的成果保留生效；修好后可直接重跑本脚本（幂等）。"
        echo "       若是 grep「无匹配」导致：属脚本缺陷（该处应容忍无匹配），请反馈上面两行。"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    } >&2
    exit "$rc"
}
trap '__on_err $? "$LINENO" "$BASH_COMMAND"' ERR

# 输出助手必须先于参数清洗定义：非法 DOMAIN 检查会调用 warn，
# set -e 下未定义命令返回 127 会直接终止脚本。
log()   { printf "\033[36m[INFO]\033[0m %s\n" "$*"; }
ok()    { printf "\033[32m[OK]\033[0m   %s\n" "$*"; }
warn()  { printf "\033[33m[WARN]\033[0m %s\n" "$*"; }
err()   { printf "\033[31m[ERROR]\033[0m %s\n" "$*"; exit 1; }

# 审计 X-24：改前所有"降级继续"的分支只 `warn`，收尾照样 `ok "部署完成！"` 并以 0 退出。
# 于是一个"日志查看器端口没监听 / nginx reload 失败、站点仍是旧配置"的部署，在 CI 里
# 被判定为成功。现在把这些分支计入 DEPLOY_PROBLEMS，收尾汇总；非 0 时默认以非 0 退出
# （`--allow-partial` 可显式接受当前状态，恢复旧的"总是 0"行为）。
DEPLOY_PROBLEMS=0
ALLOW_PARTIAL=0
problem() { DEPLOY_PROBLEMS=$((DEPLOY_PROBLEMS + 1)); warn "$@"; }

# 审计 X-10：改前脚本在 `migrate` 之后还有 collectstatic / 前端构建 / 重启服务 / nginx
# 等步骤，任何一步失败都会留下"新库结构 + 旧代码 + 服务停摆"的状态，而且**没有任何失败陷阱
# 或回滚路径**；备份也只是 `cp -a` 活库（WAL 下可能拿到不一致的副本）且失败被 `|| true` 吞掉。
# 现在：migrate 前先做一致性快照（失败即中止，因为它是唯一回滚副本），并在 EXIT trap 里
# 把"怎么退回去"的确切命令打出来。
MIGRATE_STARTED=0
MIGRATE_OK=0
PRE_MIGRATE_DB_SNAPSHOT=""
CODE_HEAD_BEFORE=""
# C2 阶段 1（WAL）后：migrate 前**必须**先停掉所有持有该库的进程，否则会出现
# 「同一库被 rollback-journal 与 WAL 两种日志模式并发访问」→ 真机实测直接
# `database disk image is malformed`（docs/真机验证报告-Debian13.md §3.5）。
SERVICES_STOPPED=0
SERVICES_TO_MANAGE="gipfel gipfel-wsgi gipfel-logviewer"

_on_exit() {
    local rc=$?
    # 失败且我们已经停过服务 → 尽力把服务拉回运行态，避免"部署失败 + 站点长时间 502"的复合故障
    if [[ "$rc" != "0" && "$SERVICES_STOPPED" == "1" ]]; then
        warn "脚本失败，但服务在 migrate 前已被停止 —— 正在尽力恢复服务运行（避免站点长时间不可用）"
        for _svc in $SERVICES_TO_MANAGE; do
            [[ -f "/etc/systemd/system/${_svc}.service" ]] || continue
            systemctl start "$_svc" 2>/dev/null || true
        done
        sleep 2
        for _svc in $SERVICES_TO_MANAGE; do
            [[ -f "/etc/systemd/system/${_svc}.service" ]] || continue
            printf '      %s = %s\n' "$_svc" "$(systemctl is-active "$_svc" 2>&1)"
        done
    fi
    if [[ "$rc" != "0" && "$MIGRATE_STARTED" == "1" && "$MIGRATE_OK" != "1" ]]; then
        echo
        warn "脚本以退出码 $rc 结束，且已执行过 migrate（或正在执行）—— 数据库结构可能已改变。"
        print_rollback_hint "$INSTALL_DIR" "$PRE_MIGRATE_DB_SNAPSHOT" "$BACKUP_DIR" "$CODE_HEAD_BEFORE"
    fi
    exit "$rc"
}
trap _on_exit EXIT

# ---------------- 参数解析 ----------------
DOMAIN=""
INSTALL_DIR="/opt/gipfel"
WITH_NGINX=0
SKIP_INSTALL_DEPS=0
FORCE_OVERWRITE=0
PUBLIC_IP=""   # 显式指定公网 IP（无域名纯 IP 部署日志查看器用）；非空则跳过自动探测与交互填写
PUBLIC_IP_SET=0  # 标记 --public-ip 是否由用户显式传入（用于结尾提示区分「用户指定」与「自动探测」）
# 审计 X-22 + 运维可用性：改前脚本把 SEED_ADMIN_PASSWORD **明文**无条件写进 stdout，
# 部署若被 `| tee deploy.log`、CI 捕获、screen/tmux 回滚缓冲或堡垒机命令记录留存，
# 拿到日志的人就能在管理员首次登录前直接以 admin 接管系统。
# 现行口径（两侧打印都遵守）：
#   · stdout 是**交互终端**（`-t 1`）→ 直接显示口令，省去运维再去 grep .env；
#   · stdout 不是终端（管道 / CI / 重定向）→ 绝不打印，只给查看命令 —— 日志里不留明文。
# `--print-seed-password` 为历史兼容开关：终端下本就显示，它已不再影响是否打印
# （非终端下也不会因它而泄露）。
PRINT_SEED_PASSWORD=0
LV_PUBLIC_IP=""  # 预初始化：set -u 下 DJANGO_ALLOWED_HOSTS 自愈块可能在其未赋值时引用（如 .env 已存在且无需纠正）
# HTTPS via Cloudflare Origin Certificate（--origin-cert）
ORIGIN_CERT=0                          # 1 = 在 vhost 中启用 443 块并填入证书路径
ORIGIN_CERT_DIR="/etc/ssl/cloudflare"  # 证书目录；文件名按 <DIR>/<域名>.pem|.key 推导
SSL_CERT=""                            # 显式覆盖证书路径（--ssl-cert），空则按域名推导
SSL_KEY=""                             # 显式覆盖私钥路径（--ssl-key）
# 日志查看器：域名 + 非标准 TLS 端口（Cloudflare 代理自定义端口形态，--logviewer-tls-port）
LOGVIEWER_TLS_PORT=""                  # 例："8443"；非空则渲染该端口块并写 LOG_VIEWER_PUBLIC_URL
LOGVIEWER_TLS_PORT_SET=0               # 用户是否显式指定过（--logviewer-tls-port / --no-logviewer-tls）

usage() {
    cat <<EOF
Usage: $0 [options]
  --domain DOMAIN              公网域名（写入 nginx server_name + 建议 HTTPS）
  --install-dir PATH           安装目录，默认 /opt/gipfel
  --public-ip IP               公网 IP（无 --domain 部署时日志查看器使用 http://<IP>:${LV_PORT:-8120}/）；
                              显式传入可跳过自动探测与交互填写，避免受限网络/非交互环境卡住
  --with-nginx                 配置 nginx 虚拟主机
  --origin-cert                启用 HTTPS，用 Cloudflare Origin Certificate（需同时给 --domain）。
                              脚本会取消 vhost 里 443 模板的注释并填入证书路径，然后 nginx -t + reload。
                              证书默认取 <--origin-cert-dir>/<域名>.pem 与 .key
  --origin-cert-dir PATH       证书目录，默认 /etc/ssl/cloudflare
  --ssl-cert PATH              显式指定证书文件（覆盖按域名推导）
  --ssl-key PATH               显式指定私钥文件（覆盖按域名推导）
  --logviewer-tls-port PORT    日志查看器的 HTTPS 端口。**--origin-cert 时默认 8443**，通常无需手传。
                               CF 只代理固定端口（HTTPS 443/2053/2083/2087/2096/8443），
                               而默认的 8120 不在其中，故 http://<域名>:8120/ 永远连不上。
                               脚本会把 LOG_VIEWER_PUBLIC_URL 写成 https://<域名>:PORT/
                               另需在云安全组入方向放行该 TCP 端口。
  --no-logviewer-tls           关闭上述端口块（用于改用 log.<域名> 子域形态的部署）
  --skip-install-deps          跳过 apt install（已知环境已装好）
  --force-overwrite            即使 INSTALL_DIR 存在也覆盖（保留 backup）
  --print-seed-password        历史兼容开关（终端下本就默认显示初始管理员口令）；
                               口令只在 stdout 为 TTY 时打印，管道/CI/重定向下绝不打印，
                               此时请自行 `sudo grep ^SEED_ADMIN_PASSWORD= <安装目录>/backend/.env`
  --allow-partial              即使有检查未通过也以退出码 0 结束（默认：有问题即非 0 退出，
                               便于 CI/自动化判定"部署是否真的成功"）
  -h, --help                   显示本帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)             DOMAIN="$2"; shift 2 ;;
        --install-dir)        INSTALL_DIR="$2"; shift 2 ;;
        --public-ip)          PUBLIC_IP="$2"; PUBLIC_IP_SET=1; shift 2 ;;
        --with-nginx)         WITH_NGINX=1; shift ;;
        --origin-cert)        ORIGIN_CERT=1; shift ;;
        --origin-cert-dir)    ORIGIN_CERT_DIR="$2"; ORIGIN_CERT=1; shift 2 ;;
        --ssl-cert)           SSL_CERT="$2"; ORIGIN_CERT=1; shift 2 ;;
        --ssl-key)            SSL_KEY="$2"; ORIGIN_CERT=1; shift 2 ;;
        --logviewer-tls-port) LOGVIEWER_TLS_PORT="$2"; LOGVIEWER_TLS_PORT_SET=1; shift 2 ;;
        --no-logviewer-tls)   LOGVIEWER_TLS_PORT="";   LOGVIEWER_TLS_PORT_SET=1; shift ;;
        --skip-install-deps)  SKIP_INSTALL_DEPS=1; shift ;;
        --force-overwrite)    FORCE_OVERWRITE=1; shift ;;
        --print-seed-password) PRINT_SEED_PASSWORD=1; shift ;;
        --allow-partial)       ALLOW_PARTIAL=1; shift ;;
        -h|--help)            usage; exit 0 ;;
        *) echo "未知参数 $1"; usage; exit 2 ;;
    esac
done

# ★ 日志查看器 TLS 端口的默认值：**默认 8443**。
#   只在 --origin-cert（=域名走 Cloudflare）时生效——因为该默认值的前提是「前置 CDN 只代理
#   固定端口」，而 8443 恰在 CF 的 HTTPS 端口白名单里（8120 不在）。非 CF 的部署（certbot
#   路线）不该被自动开一个额外端口，故此时保持关闭。
#   显式传 --logviewer-tls-port <端口> 或 --no-logviewer-tls 均会覆盖此默认。
if [[ "$LOGVIEWER_TLS_PORT_SET" != 1 && "$ORIGIN_CERT" == 1 ]]; then
    LOGVIEWER_TLS_PORT="8443"
fi

# 输入清洗：移除会破坏 sed 替换 / 正则 / nginx 配置注入的元字符（& \ /）。
# DOMAIN 仅允许主机名合法字符，PUBLIC_IP 仅允许 IP 合法字符，二者均不含上述元字符。
DOMAIN="${DOMAIN//[&\\/]/}"
PUBLIC_IP="${PUBLIC_IP//[&\\/]/}"
if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    warn "DOMAIN 含非法字符（仅允许字母/数字/.-），已忽略自动 CORS 与 nginx 配置"
    DOMAIN=""
fi

[[ $EUID -ne 0 ]] && { echo "请用 sudo 执行"; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"
BACKUP_DIR="${INSTALL_DIR}/_backup/$(date +%F_%H%M%S)"
SUDO_USER_HOME="$(eval echo ~${SUDO_USER:-$USER})"

# 规范化用户/探测得到的地址：去 http(s):// 前缀、去路径/端口后缀；IPv6 保留方括号。
# 用法：normalize_ip "$RAW"  （结果经 stdout 返回）
normalize_ip() {
    local s="$1"
    s="${s#http://}"; s="${s#https://}"
    if [[ "$s" == \[* ]]; then
        s="${s%%]*}]"     # IPv6 带方括号：仅保留 [....]
    else
        s="${s%%/*}"      # 去掉 /path 或 :port/path
        # IPv4（含点）再去掉尾随 :port；裸 IPv6 不动，避免误伤其冒号分隔
        if [[ "$s" == *.* && "$s" == *:* ]]; then
            s="${s%%:*}"
        fi
    fi
    printf '%s' "$s"
}

# 审计 X-08：日志查看器端口解析逻辑抽到 scripts/lib/deploy-common.sh，与
# update-from-github.sh 共用同一实现（避免两个脚本各写一份而漂移）。
_DEPLOY_COMMON="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deploy-common.sh"
if [[ -f "$_DEPLOY_COMMON" ]]; then
    # shellcheck source=scripts/lib/deploy-common.sh
    source "$_DEPLOY_COMMON"
fi

# 解析日志查看器 nginx 公网监听端口：取 .env 的 LOG_VIEWER_PORT（默认 8120），
# 缺失/非数字/越界（1-65535）一律兜底 8120 并告警。该端口是 nginx 监听 0.0.0.0:<port>，
# 跟 daphne 内部 127.0.0.1:8121 是两个端口（前者 .env 控制、后者 service 模板硬编码），
# 故意不等，避免同机抢端口。
# 注：优先用公共库的 `log_viewer_port`；若 lib 缺失则用下面的兜底实现（行为一致）。
if ! command -v log_viewer_port >/dev/null 2>&1; then
    log_viewer_port() {
        local _env="${1:-$INSTALL_DIR/backend/.env}"
        local _p="8120"
        if [[ -f "$_env" ]] && grep -qE '^[[:space:]]*LOG_VIEWER_PORT=' "$_env"; then
            _p=$(grep -E '^[[:space:]]*LOG_VIEWER_PORT=' "$_env" | head -1 | cut -d= -f2- \
                | tr -d '[:space:]' | sed -E "s/^['\"]//; s/['\"]$//")
            if ! [[ "$_p" =~ ^[0-9]+$ ]] || (( _p < 1 || _p > 65535 )); then
                warn "LOG_VIEWER_PORT=$_p 非法（需 1-65535 整数），回退默认 8120"
                _p="8120"
            fi
        fi
        printf '%s' "$_p"
    }
fi

# 兼容本脚本历史函数名
_log_viewer_port() { log_viewer_port "${1:-$INSTALL_DIR/backend/.env}"; }

# 多服务兜底探测公网 IP：任一可达即返回；用 timeout 硬包裹 curl，连 DNS 解析超时一并杀掉，
# 避免无外网/异常 DNS 时 curl 卡在解析阶段永不返回（curl --max-time 不限制 DNS 超时）。
# 返回空字符串表示全部失败。
_probe_public_ip() {
    local ip=""
    for svc in https://api.ipify.org https://ifconfig.me https://icanhazip.com; do
        ip="$(timeout 8 curl -s --max-time 6 "$svc" 2>/dev/null)"
        [[ -n "$ip" ]] && { printf '%s' "$ip"; return 0; }
    done
    return 1
}

check_exists() {
    [[ -f "$1" ]] || { err "缺少必要文件：$1"; }
}
check_exists "$PROJECT_ROOT/backend/requirements.txt"
check_exists "$PROJECT_ROOT/backend/.env.example"
check_exists "$PROJECT_ROOT/frontend/package.json"
check_exists "$PROJECT_ROOT/deploy/gipfel.service"
check_exists "$PROJECT_ROOT/deploy/gipfel-wsgi.service"
check_exists "$PROJECT_ROOT/deploy/logviewer.service"
check_exists "$PROJECT_ROOT/deploy/nginx-gipfel.conf"

# ---------------- 1. 系统依赖 ----------------
if [[ $SKIP_INSTALL_DEPS -eq 0 ]]; then
    log "安装系统依赖：python3-venv python3-dev nodejs npm nginx rsync curl ca-certificates"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y \
        python3 python3-venv python3-dev python3-pip \
        curl ca-certificates gnupg lsb-release \
        nginx openssl rsync

    # NodeSource 20 LTS（apt 默认 node 太老）
    # 审计 X-06：改前是 `curl -fsSL https://deb.nodesource.com/setup_20.x | bash -` ——
    # 以 **root** 管道执行远端脚本，既不校验 GPG 也不比对哈希，而 `setup_20.x` 是浮动别名
    # （上游会原地覆盖同一 URL）。企业 TLS 中间盒、被污染的 DNS 或上游账户被入侵，
    # 都能在部署机上直接拿到 root。现在改为官方文档的 apt-keyring 方式：
    #   下载 GPG 公钥 → gpg --dearmor 落到 keyring → sources.list.d 显式 signed-by → apt 校验签名安装。
    # 全程不使用 `| bash`。
    if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]]; then
        NODESOURCE_KEYRING="/usr/share/keyrings/nodesource.gpg"
        NODESOURCE_LIST="/etc/apt/sources.list.d/nodesource.list"
        NODE_MAJOR=20
        log "添加 NodeSource ${NODE_MAJOR}.x 官方 apt 仓库（GPG 校验，不使用 curl|bash）"
        curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
            -o /tmp/nodesource-repo.gpg.key \
            || err "下载 NodeSource GPG 公钥失败（检查网络/DNS；如用内网镜像请手动配置 apt 源）"
        # 至少把指纹/哈希打印出来，便于事后审计（发布方公布的 key 为 6F71 54B7 7A96 86F0）
        sha256sum /tmp/nodesource-repo.gpg.key || true
        gpg --dearmor --yes -o "$NODESOURCE_KEYRING" /tmp/nodesource-repo.gpg.key \
            || err "gpg --dearmor 失败，无法建立 NodeSource keyring"
        chmod 0644 "$NODESOURCE_KEYRING"
        rm -f /tmp/nodesource-repo.gpg.key
        echo "deb [signed-by=${NODESOURCE_KEYRING}] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
            > "$NODESOURCE_LIST"
        apt-get update -y
        apt-get install -y nodejs
    fi

    ( command -v npm >/dev/null && command -v node >/dev/null ) || err "node/npm 未安装成功"
    ok "系统依赖完成：python=$(python3 --version) node=$(node -v) npm=$(npm -v)"
else
    log "跳过系统依赖安装（--skip-install-deps）"
fi

# rsync 是代码同步的硬依赖（即便 --skip-install-deps 也必须存在，否则下方 rsync 直接 command not found）
command -v rsync >/dev/null 2>&1 || err "缺少 rsync，请先执行：apt-get install -y rsync（或重跑本脚本去掉 --skip-install-deps 以自动安装）"

# ---------------- 1.5 创建专用运行用户（审计 X-30）----------------
# 改前是 `if ! id gipfel; then useradd … || true; fi`：失败被 `|| true` 吞掉，脚本带着
# "没有运行用户"继续跑完 migrate + 前端构建，最后在下方 `chown -R gipfel:gipfel` 处
# 以 `chown: invalid user: 'gipfel:gipfel'` 报错退出（真机事故）。
# 现在交给公共库的 ensure_runtime_user：用户与组一起校验、组已存在时用 -g 复用、失败即中止。
command -v ensure_runtime_user >/dev/null 2>&1 \
    || err "缺少 scripts/lib/deploy-common.sh（ensure_runtime_user 未定义），无法创建运行用户；请确认 scripts/lib/ 随代码一起部署"
ensure_runtime_user gipfel "$INSTALL_DIR"

# ---------------- 2. 同步代码，必要时备份旧数据 ----------------
if [[ -d "$INSTALL_DIR/backend" && -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
    log "发现现有部署 → 备份到 $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    # 审计 X-10：数据库是整个部署里**唯一无法从代码重建**的东西，它的副本必须自洽。
    # 改前是 `cp -a <活库> … || true`：WAL 模式下可能抓到不一致的快照，而且失败被静默吞掉。
    CODE_HEAD_BEFORE="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo '')"
    if [[ -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
        if snapshot_sqlite_consistent "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/db.sqlite3"; then
            PRE_MIGRATE_DB_SNAPSHOT="$BACKUP_DIR/db.sqlite3"
            ok "数据库一致性快照已就绪：$PRE_MIGRATE_DB_SNAPSHOT"
        else
            err "无法为现有数据库生成一致性快照（$BACKUP_DIR/db.sqlite3）—— 没有它就无法回滚，已中止。请先手动备份后重跑。"
        fi
    fi
    cp -a "$INSTALL_DIR/backend/uploads"    "$BACKUP_DIR/" 2>/dev/null || warn "uploads 备份失败（可稍后手动复制）"
    cp -a "$INSTALL_DIR/backend/.env"       "$BACKUP_DIR/" 2>/dev/null || warn ".env 备份失败（可稍后手动复制）"
fi

log "同步代码 → $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
# 仅同步 backend / frontend / deploy 三个源码目录；避免 node_modules / .venv / db.sqlite3
# （--delete 只删目标端已同步子目录内的旧文件）
#
# ★ `--exclude=/snapshots/` 的**前导斜杠不可省**：rsync 的排除模式若不带 `/` 是「任意层级同名目录」，
#   而 `backend/apps/snapshots/` 是 **Django app**（在 INSTALLED_APPS 里）—— 写成 `--exclude snapshots`
#   会连同这个 app 一起排除，再叠加 `--delete` 就会把线上该 app **删掉**，服务启动直接
#   `ModuleNotFoundError: No module named 'apps.snapshots'`。带前导斜杠后只匹配
#   **传输根**下的 `snapshots/`（即历史快照归档目录），app 目录不受影响。
#   （真机复现记录见 docs/真机验证报告-Debian13.md §3.4）
# ★ `db.sqlite3*` 必须带通配符：C2 阶段 1 之后源目录里的 `db.sqlite3` 常处于 WAL 模式，
#   旁边会有 `db.sqlite3-wal` / `db.sqlite3-shm`。只排除 `db.sqlite3` 会把**源库的 WAL**
#   拷到线上主库旁边 —— SQLite 会拿一个「属于另一个数据库的 WAL」去恢复线上库，直接得到
#   `database disk image is malformed`，且此后备份/迁移全部失败（真机事故：
#   docs/真机验证报告-Debian13.md §3.6）。
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude '*.pyc' \
    --exclude node_modules --exclude dist --exclude 'db.sqlite3*' \
    --exclude uploads --exclude logs --exclude '.env' --exclude=/snapshots/ \
    "$PROJECT_ROOT/backend/"  "$INSTALL_DIR/backend/"
rsync -a --delete --exclude node_modules --exclude dist \
    "$PROJECT_ROOT/frontend/" "$INSTALL_DIR/frontend/"
rsync -a --delete "$PROJECT_ROOT/deploy/"   "$INSTALL_DIR/deploy/"

# 恢复备份的 uploads/.env
if [[ -d "$BACKUP_DIR/uploads" ]]; then
    mkdir -p "$INSTALL_DIR/backend/uploads"
    rsync -a "$BACKUP_DIR/uploads/" "$INSTALL_DIR/backend/uploads/"
fi
if [[ -f "$BACKUP_DIR/.env" && ! -f "$INSTALL_DIR/backend/.env" ]]; then
    cp -a "$BACKUP_DIR/.env" "$INSTALL_DIR/backend/.env"
fi
# 恢复备份的数据库（核心业务数据，必须随部署保留，否则重部署会丢失全部数据）
# ★ WAL 安全性说明（C2 阶段 1 起 SQLite 为 WAL 模式）：
#   本分支只在**目标库不存在**（`! -f`）时执行 —— 即"新建/库被删"的独占场景，
#   此时 install 目录下不存在 db.sqlite3-wal/-shm（没有库就没有 WAL），
#   而 $BACKUP_DIR/db.sqlite3 是 snapshot_sqlite_consistent（VACUUM INTO）产出的自洽副本，
#   因此这里的 cp 是 WAL 安全的。**不要**把它改成"库存在时也覆盖"——
#   那时必须 ① 先停服 ② 先 rm -f db.sqlite3-wal/-shm，否则旧 WAL 会被重放到恢复出来的库上。
if [[ -f "$BACKUP_DIR/db.sqlite3" && ! -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
    cp -a "$BACKUP_DIR/db.sqlite3" "$INSTALL_DIR/backend/db.sqlite3"
    log "已从备份恢复数据库 $BACKUP_DIR/db.sqlite3（目标库原本不存在＝无 WAL 残留，属安全场景）"
fi

# 首次部署无 .env → 从 example 复制，生成随机 JWT_SECRET
if [[ ! -f "$INSTALL_DIR/backend/.env" ]]; then
    log "首次部署：生成后端 .env"
    echo "[DIAG] .env 不存在，准备从 .env.example 复制（$(date +%T)）" >&2
    cp "$INSTALL_DIR/backend/.env.example" "$INSTALL_DIR/backend/.env"
    echo "[DIAG] .env.example 复制完成（$(date +%T)）" >&2
    # 生成随机密钥：用 /dev/urandom 直接取字节（base64），不依赖 openssl 也不等系统熵，绝不阻塞。
    # 新装系统 openssl rand 偶发因熵不足变慢，故改用 head -c 读 urandom 兜底。
    SECRET="$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=')"
    echo "[DIAG] JWT_SECRET 生成完成（$(date +%T)）" >&2
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${SECRET}|" "$INSTALL_DIR/backend/.env"
    # Django SECRET_KEY：session / CSRF 签名用，与 JWT 密钥分离（安全隔离原则）。
    # .env.example 中通常注释掉（# DJANGO_SECRET_KEY=""），需要取消注释并写入随机值。
    DJANGO_SK="$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=')"
    if grep -qE '^#?[[:space:]]*DJANGO_SECRET_KEY=' "$INSTALL_DIR/backend/.env"; then
        # 存在（可能注释），取消注释并覆盖
        sed -i -E "s|^#?[[:space:]]*DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=${DJANGO_SK}|" "$INSTALL_DIR/backend/.env"
    else
        # 不存在，追加
        echo "DJANGO_SECRET_KEY=${DJANGO_SK}" >> "$INSTALL_DIR/backend/.env"
    fi
    echo "[DIAG] DJANGO_SECRET_KEY 生成完成（$(date +%T)）" >&2
    # 显式关闭 DEBUG（.env.example 可能无此行，确保生产环境 DEBUG=false）
    if ! grep -q '^DEBUG=' "$INSTALL_DIR/backend/.env"; then
        echo 'DEBUG=false' >> "$INSTALL_DIR/backend/.env"
    else
        sed -i 's/^DEBUG=.*/DEBUG=false/' "$INSTALL_DIR/backend/.env"
    fi
    if [[ -n "$DOMAIN" ]]; then
        # 取消注释并设置 CORS 白名单（兼容已注释 #CORS_ORIGIN=... 与未注释两种写法）
        sed -i -E "s|^#?[[:space:]]*CORS_ORIGIN=.*|CORS_ORIGIN=https://${DOMAIN},http://${DOMAIN}|" "$INSTALL_DIR/backend/.env"
        grep -q '^CORS_ORIGIN=' "$INSTALL_DIR/backend/.env" || \
            echo "CORS_ORIGIN=https://${DOMAIN},http://${DOMAIN}" >> "$INSTALL_DIR/backend/.env"
        # CSRF Origin 白名单：/admin 登录表单等带 Origin 的 POST 必须命中，否则 403
        sed -i -E "s|^#?[[:space:]]*DJANGO_CSRF_TRUSTED_ORIGINS=.*|DJANGO_CSRF_TRUSTED_ORIGINS=https://${DOMAIN},http://${DOMAIN}|" "$INSTALL_DIR/backend/.env"
        grep -q '^DJANGO_CSRF_TRUSTED_ORIGINS=' "$INSTALL_DIR/backend/.env" || \
            echo "DJANGO_CSRF_TRUSTED_ORIGINS=https://${DOMAIN},http://${DOMAIN}" >> "$INSTALL_DIR/backend/.env"
    else
        # 无域名（纯 IP）部署：日志查看器走 nginx 公网 LOG_VIEWER_PORT 端口（默认 8120，
        # 见 _log_viewer_port），显式下发【公网】地址，避免前端 /api/version 把 Host
        # 推导成错误的 https://log.<IP>/ 或误用内网 IP。
        # 重要：必须用公网 IP（用户从公网访问），不能用 hostname -I 首地址（通常为内网/私网 IP）。
        #
        # 优先顺序：--public-ip 显式传入 > 多服务探测（任何一步成功都不进入下一步，
        #   确保非交互/受限网络环境永不卡住）。不再提供交互式手动填写——脚本以
        #   exec 0</dev/null 运行（非 TTY），手动 read 恒为 EOF，属死代码，已移除。
        if [[ -n "$PUBLIC_IP" ]]; then
            LV_PUBLIC_IP="$(normalize_ip "$PUBLIC_IP")"
            ok "使用 --public-ip 显式指定的公网 IP：${LV_PUBLIC_IP}"
        else
            # 用 timeout 硬包裹 curl：curl --max-time 不限制 DNS 解析超时，无外网/异常 DNS 时
            # 会卡在解析阶段永不返回；timeout 连 DNS 一起杀掉，保证 N 秒内必返回（空=失败）。
            LV_PUBLIC_IP="$(_probe_public_ip)" || true
        fi
        # 在使用 LV_PORT 前必须先定义（set -u 下未定义变量会报错退出）
        LV_PORT="$(_log_viewer_port "$INSTALL_DIR/backend/.env")"
        if [[ -n "$LV_PUBLIC_IP" ]]; then
            LV_PUBLIC_IP="$(normalize_ip "$LV_PUBLIC_IP")"
            # IPv6 需加方括号：http://[IPv6]:${LV_PORT}/
            if [[ "$LV_PUBLIC_IP" == *:* && "$LV_PUBLIC_IP" != \[* ]]; then
                echo "LOG_VIEWER_PUBLIC_URL=http://[${LV_PUBLIC_IP}]:${LV_PORT}/" >> "$INSTALL_DIR/backend/.env"
            else
                echo "LOG_VIEWER_PUBLIC_URL=http://${LV_PUBLIC_IP}:${LV_PORT}/" >> "$INSTALL_DIR/backend/.env"
            fi
            ok "日志查看器地址已写入 LOG_VIEWER_PUBLIC_URL（公网 IP：${LV_PUBLIC_IP}，端口：${LV_PORT}）。"
        else
            warn "未能自动获取公网 IP（探测失败），未写入 LOG_VIEWER_PUBLIC_URL；日志查看器地址将由后端按请求 Host 推导（请确保经公网 IP 访问）。"
        fi
    fi
    # 日志查看器防直连令牌密钥：必须生成强随机值，且部署幂等——无论 .env.example 是否
    # 已带默认值（避免自带的弱默认值残留在生产环境），首次部署都强制覆盖为随机串。
    LVSECRET="$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=')"
    if grep -q '^LOGVIEWER_SECRET_KEY=' "$INSTALL_DIR/backend/.env"; then
        sed -i -E "s|^LOGVIEWER_SECRET_KEY=.*|LOGVIEWER_SECRET_KEY=${LVSECRET}|" "$INSTALL_DIR/backend/.env"
    else
        echo "LOGVIEWER_SECRET_KEY=${LVSECRET}" >> "$INSTALL_DIR/backend/.env"
    fi
    # 审计 X-22：只报告"做了什么"，不枚举密钥名
    echo "[DIAG] 日志查看器防直连密钥已生成并写入（$(date +%T)）" >&2

    # 默认管理员密码：首次部署自动生成强随机密码（settings.py 也会兜底生成，但这里显式写入 .env 便于运维查看）
    ADMIN_PW="$(head -c 16 /dev/urandom | base64 | tr -d '\n+/=' | head -c 20)"
    if grep -q '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env"; then
        sed -i -E "s|^SEED_ADMIN_PASSWORD=.*|SEED_ADMIN_PASSWORD=${ADMIN_PW}|" "$INSTALL_DIR/backend/.env"
    else
        echo "SEED_ADMIN_PASSWORD=${ADMIN_PW}" >> "$INSTALL_DIR/backend/.env"
    fi
    if [[ -t 1 ]]; then
        ok "管理员密码已生成：admin / ${ADMIN_PW}（首次登录强制改密）"
    else
        ok "管理员初始密码已生成并写入 ${INSTALL_DIR}/backend/.env 的 SEED_ADMIN_PASSWORD（首次登录强制改密）"
        ok "需要查看：sudo grep '^SEED_ADMIN_PASSWORD=' ${INSTALL_DIR}/backend/.env"
        if [[ "$PRINT_SEED_PASSWORD" == "1" ]]; then
            warn "--print-seed-password 已给出，但 stdout 不是终端（管道/CI）—— 为避免口令进入日志，此处不打印。"
        fi
    fi
    echo "[DIAG] 管理员初始口令已生成并写入（$(date +%T)）" >&2

    # 审计 X-22：改前这里把 4 个密钥名逐个枚举到 stderr —— 单行不敏感提示即可
    echo "[DIAG] 首次部署所需密钥/口令已全部生成并写入（$(date +%T)）" >&2
fi

# 解析日志查看器 nginx 公网监听端口（.env 已就绪，vhost/URL/防火墙/输出提示全流程共用），
# 缺失/非法一律兜底 8120（见 _log_viewer_port 函数注释）。这里是 vhost 渲染前的最早
# 可用点，必须在 vhost 渲染与 ufw 放行之前完成。
LV_PORT="$(_log_viewer_port "$INSTALL_DIR/backend/.env")"
log "日志查看器公网监听端口：${LV_PORT}（daphne 内部仍绑 8121，两者解耦避免同机抢端口）"

# C1-a：gunicorn(WSGI) 的本地端口（默认 8002；.env 的 GIPFEL_WSGI_PORT 可覆盖）。
# 与 deploy/gipfel-wsgi.service 的 Environment="GIPFEL_WSGI_PORT=8002" 同一默认值：
#   · 健康检查（WSGI :8002/api/health）用它；
#   · vhost 渲染后校验/同步 upstream gipfel_django 的端口用它
#     （模板里写死 127.0.0.1:8002 是为了让默认拓扑一眼可见；.env 改了端口时脚本会把
#      渲染产物里的 upstream 一起改掉，避免"unit 换了端口、nginx 还指着旧端口 → 502"）。
_WSGI_PORT="$(gipfel_wsgi_port "$INSTALL_DIR/backend/.env")"
log "WSGI(gunicorn) 本地监听端口：${_WSGI_PORT}（nginx upstream gipfel_django 指向它；仅绑回环，不对公网暴露）"

# 自愈：无域名部署且 .env 已存在时，纠正/补全 LOG_VIEWER_PUBLIC_URL。
#   - 显式 --public-ip：无论当前有无/对错，都以它为准写入（覆盖内网 IP 或缺失该行）
#   - 否则：仅当当前值指向内网/私网 IP 才纠正；已是公网 IP/域名/缺失行则不动
# 公网 IP 探测失败则移除该行，改由后端按请求 Host 推导（nginx 透传 $host=公网 IP），
# 避免内网 IP 持续生效。
if [[ -z "$DOMAIN" && -f "$INSTALL_DIR/backend/.env" ]]; then
    # ★ 整条管道必须容忍「无匹配」：.env 里没有该行时 grep 退出 1，set -o pipefail 下
    #   整条管道失败；而这行是**变量赋值**，退出码即命令替换的退出码 → set -e 静默终止脚本。
    LV_CUR="$(grep -E '^LOG_VIEWER_PUBLIC_URL=' "$INSTALL_DIR/backend/.env" | tail -n1 | sed -E 's#^LOG_VIEWER_PUBLIC_URL=https?://##; s#[/:].*##' || true)"
    LV_NEED_FIX=0
    if [[ -n "$PUBLIC_IP" ]]; then
        LV_NEED_FIX=1   # 显式指定：强制以 --public-ip 为准（覆盖内网 IP 或缺失行）
    elif [[ -n "$LV_CUR" ]]; then
        if [[ "$LV_CUR" =~ ^10\. ]] || \
           [[ "$LV_CUR" =~ ^192\.168\. ]] || \
           [[ "$LV_CUR" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]] || \
           [[ "$LV_CUR" =~ ^169\.254\. ]] || \
           [[ "$LV_CUR" =~ ^127\. ]]; then
            LV_NEED_FIX=1
        fi
    fi
    if [[ $LV_NEED_FIX -eq 1 ]]; then
        if [[ -n "$PUBLIC_IP" ]]; then
            # 显式指定 --public-ip：直接以此为准，跳过探测（确保受限网络下也能纠正）
            LV_PUBLIC_IP="$(normalize_ip "$PUBLIC_IP")"
            if grep -q '^LOG_VIEWER_PUBLIC_URL=' "$INSTALL_DIR/backend/.env"; then
                if [[ "$LV_PUBLIC_IP" == *:* && "$LV_PUBLIC_IP" != \[* ]]; then
                    sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://[${LV_PUBLIC_IP}]:${LV_PORT}/|" "$INSTALL_DIR/backend/.env"
                else
                    sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://${LV_PUBLIC_IP}:${LV_PORT}/|" "$INSTALL_DIR/backend/.env"
                fi
            else
                echo "LOG_VIEWER_PUBLIC_URL=http://${LV_PUBLIC_IP}:${LV_PORT}/" >> "$INSTALL_DIR/backend/.env"
            fi
            warn "已按 --public-ip 写入 LOG_VIEWER_PUBLIC_URL=${LV_PUBLIC_IP}:${LV_PORT}（原值：${LV_CUR:-无}）。"
        else
            # 多服务兜底探测公网 IP（任一可达即可）；均失败则回退「移除该行，交给后端按 Host 推导」
            LV_PUBLIC_IP="$(_probe_public_ip)" || true
            if [[ -n "$LV_PUBLIC_IP" && "$LV_PUBLIC_IP" != "$LV_CUR" ]]; then
                LV_PUBLIC_IP="$(normalize_ip "$LV_PUBLIC_IP")"
                if [[ "$LV_PUBLIC_IP" == *:* && "$LV_PUBLIC_IP" != \[* ]]; then
                    sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://[${LV_PUBLIC_IP}]:${LV_PORT}/|" "$INSTALL_DIR/backend/.env"
                else
                    sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://${LV_PUBLIC_IP}:${LV_PORT}/|" "$INSTALL_DIR/backend/.env"
                fi
                warn "检测到 LOG_VIEWER_PUBLIC_URL 指向内网 IP(${LV_CUR})，已自动纠正为公网 IP(${LV_PUBLIC_IP})。"
            else
                # 公网 IP 探测全部失败：直接移除该行，避免内网 IP 继续生效；
                # 后端 VersionView 会按请求 Host（nginx 透传 $host=公网 IP）推导为
                # http://<公网IP>:${LV_PORT}/（端口取 .env 的 LOG_VIEWER_PORT）
                sed -i -E "/^LOG_VIEWER_PUBLIC_URL=/d" "$INSTALL_DIR/backend/.env"
                warn "公网 IP 探测失败，已移除 .env 中指向内网 IP(${LV_CUR}) 的 LOG_VIEWER_PUBLIC_URL，改由后端按请求 Host 推导公网地址。"
            fi
        fi
    fi
fi

# 自愈：DJANGO_ALLOWED_HOSTS 必须包含公网访问入口（域名或公网 IP）。
#   缺失时 Django 对一切非回环 Host 的请求返回原生 400 Bad Request（登录/健康检查
#   全部失败，前端表现为「请求参数错误」）。幂等：白名单里已有该条目则不重复追加。
#   优先顺序：--domain > --public-ip > 自动探测公网 IP。
#   ★ 域名模式下必须**同时**加入 log.<DOMAIN>：日志查看器是独立 Django 服务，它自己的
#     ALLOWED_HOSTS 靠本项兜底纳入（见 backend/logviewer/logviewer/settings.py 读取
#     DJANGO_ALLOWED_HOSTS 的那段）。少了这个子域，nginx 以 Host: log.<域名> 反代过去
#     会被日志查看器以 400 拒绝——现象是「域名部署后主站正常、日志查看器打不开」，
#     很难联想到是 Host 头白名单问题。
if [[ -f "$INSTALL_DIR/backend/.env" ]]; then
    AH_ENTRIES=()
    if [[ -n "$DOMAIN" ]]; then
        AH_ENTRIES+=("$DOMAIN" "log.$DOMAIN")
    else
        if [[ -z "$LV_PUBLIC_IP" && -n "$PUBLIC_IP" ]]; then
            LV_PUBLIC_IP="$(normalize_ip "$PUBLIC_IP")"
        fi
        if [[ -z "$LV_PUBLIC_IP" ]]; then
            LV_PUBLIC_IP="$(_probe_public_ip)" || true
            [[ -n "$LV_PUBLIC_IP" ]] && LV_PUBLIC_IP="$(normalize_ip "$LV_PUBLIC_IP")"
        fi
        if [[ -n "$LV_PUBLIC_IP" ]]; then
            AH_ENTRIES+=("$LV_PUBLIC_IP")
        fi
    fi
    if [[ ${#AH_ENTRIES[@]} -gt 0 ]]; then
        # 审计 X-11（master 合并后重做）：改前/原 master 版本用
        #     sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|&,${_ah}|" .env
        # 来追加条目 —— `&` 是「整行匹配文本」，于是每追加一个新公网入口就把
        # 旧条目**再复制一遍**（`A,B` → 追加 C 得 `A,B,A,B,C`），公网 IP/域名一变
        # 白名单里就堆历史值；同名键多行时还会被逐行改写（python-dotenv 只认第一条）。
        # 现在改为调用 lib 的 append_env_entry：逗号分隔去重合并 + 写回**唯一一行**。
        _ah_join="$(IFS=,; echo "${AH_ENTRIES[*]}")"
        _ah_value="$(append_env_entry "$INSTALL_DIR/backend/.env" "DJANGO_ALLOWED_HOSTS" \
            "$_ah_join" "localhost,127.0.0.1")"
        ok "DJANGO_ALLOWED_HOSTS 已写入（去重合并、唯一一行）：${_ah_value}"
    else
        warn "未能确定公网入口（域名/公网 IP 均为空），DJANGO_ALLOWED_HOSTS 未修改；若经公网访问出现 400，请手动在 backend/.env 加入 DJANGO_ALLOWED_HOSTS=<公网IP>,localhost"
    fi

    # ---- 域名形态：脚本统一决定日志查看器公网地址（自动，无需人工改 .env）----
    #   · 有 --logviewer-tls-port（--origin-cert 时默认 8443）→ https://<域名>:<端口>/
    #   · 否则（log.<域名> 子域形态）                        → https://log.<域名>/
    #
    # 为什么由脚本写死，而不是留给后端按请求 Host 推导：
    #   /api/version 的逻辑是「优先用 LOG_VIEWER_PUBLIC_URL，没有才按 Host 推导」。两处都可能错：
    #     · .env 里残留早期无域名部署写入的 http://<公网IP>:8120/ → 按钮永远指向它（真实故障）
    #     · Host 若为 IP（如用 IP 访问页面）→ 推导出 http://<IP>:8120/，在 CF 代理下必然打不开
    #   显式写死之后只有一个权威值，且脚本收尾能自检（见文件末尾「部署自检」）。
    #
    # 为什么换端口：CF 只代理固定端口（HTTP 80/8080/8880/2052/2082/2086/2095，
    #   HTTPS 443/2053/2083/2087/2096/8443），默认 8120 不在其中。
    if [[ -n "$DOMAIN" ]]; then
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            if [[ ! "$LOGVIEWER_TLS_PORT" =~ ^[0-9]+$ ]] \
               || (( LOGVIEWER_TLS_PORT < 1 || LOGVIEWER_TLS_PORT > 65535 )); then
                err "--logviewer-tls-port 需为 1-65535 的整数，收到：${LOGVIEWER_TLS_PORT}"
            fi
            if [[ "$ORIGIN_CERT" != 1 ]]; then
                err "--logviewer-tls-port 需要与 --origin-cert 一起使用（该端口块本身要 TLS 证书，否则按钮会指向一个没人监听的端口）"
            fi
            _lv_url="https://${DOMAIN}:${LOGVIEWER_TLS_PORT}/"
        else
            _lv_url="https://log.${DOMAIN}/"
        fi
        # 先删后加：幂等，且不受「行不存在时 sed 空操作」影响（曾因此静默无效）
        sed -i -E '/^LOG_VIEWER_PUBLIC_URL=/d' "$INSTALL_DIR/backend/.env"
        echo "LOG_VIEWER_PUBLIC_URL=${_lv_url}" >> "$INSTALL_DIR/backend/.env"
        ok "LOG_VIEWER_PUBLIC_URL=${_lv_url}（前端「日志查看器」按钮将指向它）"

        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            warn "别忘了在**云安全组**入方向放行 TCP ${LOGVIEWER_TLS_PORT}（脚本只能放行本机 ufw，管不到云侧）。"
        elif ! getent hosts "log.${DOMAIN}" >/dev/null 2>&1; then
            # 子域形态的自动前置检查：没有 DNS 记录时按钮必然打不开，这里直接说清
            warn "log.${DOMAIN} 解析不到（无 DNS 记录）——日志查看器按钮将打不开。"
            warn "  若无法添加该三级记录，请改用 --origin-cert（TLS 端口默认 8443，不需要该子域）。"
        fi
    fi
fi

mkdir -p "$INSTALL_DIR/backend/uploads" "$INSTALL_DIR/backend/logs" "$INSTALL_DIR/backend/snapshots"
ok "代码同步完成"

# ---------------- 3. 虚拟环境 + pip ----------------
cd "$INSTALL_DIR/backend"
if [[ ! -d .venv ]]; then
    log "创建 Python 虚拟环境并安装依赖"
    python3 -m venv .venv
    ".venv/bin/pip" install --upgrade pip setuptools wheel
else
    log "虚拟环境存在，更新依赖"
fi
".venv/bin/pip" install -r requirements.txt
ok "Python 依赖安装完成"

# ---------------- 3.5 停服务：migrate 前必须先停（C2 阶段 1 起为硬要求） ----------------
# 为什么必须：C2 阶段 1 把 SQLite 切到 WAL。`migrate` 会把库从 rollback journal 切到 WAL，
# 而**旧版本的服务进程仍在运行**（它用 rollback journal 持有连接）——同一库被两种日志模式
# 并发访问，真机实测直接得到 `database disk image is malformed`（首次部署即复现，
# 见 docs/真机验证报告-Debian13.md §3.5）；此时脚本的一致性快照备份、迁移与线上库全部不可用。
# 停服后 migrate 才能安全切换并独占写权。失败的 EXIT trap 会尽力把服务拉回运行态。
log "停止后端服务（migrate 前必须停止：WAL 切换要求没有其它连接持有该库）"
for _svc in $SERVICES_TO_MANAGE; do
    if [[ -f "/etc/systemd/system/${_svc}.service" ]]; then
        if systemctl is-active --quiet "$_svc" 2>/dev/null; then
            if systemctl stop "$_svc" 2>/dev/null; then
                ok "已停止 $_svc"
                SERVICES_STOPPED=1
            else
                warn "停止 $_svc 失败（继续；若 migrate 报锁错误请手动停服后重跑）"
            fi
        fi
    fi
done
if [[ "$SERVICES_STOPPED" == "1" ]]; then
    sleep 1
    for _svc in $SERVICES_TO_MANAGE; do
        [[ -f "/etc/systemd/system/${_svc}.service" ]] || continue
        systemctl is-active --quiet "$_svc" 2>/dev/null && warn "$_svc 仍在运行（migrate 可能失败）"
    done
fi

# ---------------- 4. 数据库迁移 + seed 默认 admin ----------------
log "执行 migrate（首次会自动建 admin，密码自动生成或取自 .env SEED_ADMIN_PASSWORD）"
".venv/bin/python" manage.py check --fail-level ERROR
# 审计 X-10：从这里开始数据库结构可能改变；若后续任一步失败，EXIT trap 会打印回滚指引。
MIGRATE_STARTED=1
".venv/bin/python" manage.py migrate --noinput
".venv/bin/python" manage.py collectstatic --noinput
# 日志查看器静态资源（独立项目，settings=logviewer.settings）
log "收集日志查看器静态资源"
cd "$INSTALL_DIR/backend/logviewer"
"$INSTALL_DIR/backend/.venv/bin/python" manage.py collectstatic --noinput --settings=logviewer.settings
cd "$INSTALL_DIR/backend"
MIGRATE_OK=1
ok "数据库迁移完成，静态资源收集完成"

# ---------------- 5. 前端构建 ----------------
log "构建前端（npm ci + build）"
cd "$INSTALL_DIR/frontend"
if [[ ! -d node_modules ]]; then
    npm ci --no-audit --no-fund
else
    # 有 package-lock 变更时增量装
    npm install --no-audit --no-fund 2>/dev/null || npm ci --no-audit --no-fund
fi
npm run build
rm -rf "$INSTALL_DIR/frontend-dist"
mkdir -p "$INSTALL_DIR/frontend-dist"
cp -a dist/. "$INSTALL_DIR/frontend-dist/"
# 审计 X-30：chown 前再确认运行用户/组存在 —— 避免上游建用户失败再次以
# “chown: invalid user” 的形式暴露（本仓库真实事故：排查方向被带偏到权限问题）。
id gipfel >/dev/null 2>&1 && getent group gipfel >/dev/null 2>&1 \
    || err "运行用户/组 gipfel 不存在，无法切换文件归属（请检查 1.5 步 ensure_runtime_user 的输出）"
chown -R gipfel:gipfel "$INSTALL_DIR/frontend-dist"
ok "前端构建完成 → $INSTALL_DIR/frontend-dist"

# 以 root 身份完成 venv/pip/migrate/collectstatic/build 后，统一把整棵安装树归属运行用户 gipfel，
# 否则 systemd 以 gipfel 启动时对 root 所有的 db.sqlite3/uploads/logs 无写权限会启动失败。
chown -R gipfel:gipfel "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/backend/.env" 2>/dev/null || true
ok "文件归属已切换为 gipfel（运行时可写 db/uploads/logs），.env 权限收紧为 600"

# ---------------- 6. systemd unit ----------------
# C1-a：现在是**三个** unit ——
#   gipfel.service        daphne(ASGI)  127.0.0.1:8000  → 只承载 /socket.io/（+ 回环兼容的完整 Django）
#   gipfel-wsgi.service   gunicorn(WSGI) 127.0.0.1:8002 → 承载 /api/、/admin/（多 worker 多线程）
#   gipfel-logviewer.service 日志查看器 daphne 127.0.0.1:8121
log "写入 systemd 服务 gipfel.service / gipfel-wsgi.service / gipfel-logviewer.service"
# masked 自愈：mask 有两种落点——永久（/etc/systemd/system/<unit> → /dev/null）与
# 运行时（/run/systemd/system/<unit> → /dev/null，且 /run 优先级高于 /etc，会遮蔽 /etc 的真实 unit）。
# cp -f 会跟随 /dev/null 软链把内容写进 /dev/null 而非替换软链，故必须先无条件 unmask
# （systemctl unmask 同时清理两处）并删除两处软链，再写入真实 unit。
for _svc in gipfel.service gipfel-wsgi.service gipfel-logviewer.service; do
    if [[ -L "/etc/systemd/system/$_svc" || -L "/run/systemd/system/$_svc" ]]; then
        warn "检测到 $_svc 存在 mask 软链，执行 unmask 解除"
    fi
    systemctl unmask "$_svc" 2>/dev/null || true
    rm -f "/etc/systemd/system/$_svc" "/run/systemd/system/$_svc"
done
# 渲染到独立临时文件：PROJECT_ROOT 与 INSTALL_DIR 同目录（原地部署）时，
# 直接 > 到 deploy/ 下的目标会先清空模板、sed 读到空内容，模板与产物一起变 0 字节
# ——systemd 把空 unit 文件按 masked 处理（真实事故）。
_tmp_unit="$(mktemp /tmp/gipfel.unit.XXXXXX)"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$PROJECT_ROOT/deploy/gipfel.service" > "$_tmp_unit"
rm -f /etc/systemd/system/gipfel.service
cp -f "$_tmp_unit" /etc/systemd/system/gipfel.service
rm -f "$_tmp_unit"

# C1-a：gunicorn(WSGI) unit —— 与上面同款渲染（只替换 __INSTALL_DIR__；
# 端口/worker/线程默认值在 unit 的 Environment= 里，可被 backend/.env 的
# GIPFEL_WSGI_PORT/WORKERS/THREADS/TIMEOUT 覆盖，脚本与模板都不写死）
_tmp_unit="$(mktemp /tmp/gipfel.unit.XXXXXX)"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$PROJECT_ROOT/deploy/gipfel-wsgi.service" > "$_tmp_unit"
rm -f /etc/systemd/system/gipfel-wsgi.service
cp -f "$_tmp_unit" /etc/systemd/system/gipfel-wsgi.service
rm -f "$_tmp_unit"

LV_UNIT_FILE="$INSTALL_DIR/deploy/logviewer.service"
_tmp_unit="$(mktemp /tmp/gipfel.unit.XXXXXX)"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$PROJECT_ROOT/deploy/logviewer.service" > "$_tmp_unit"
rm -f /etc/systemd/system/gipfel-logviewer.service
cp -f "$_tmp_unit" /etc/systemd/system/gipfel-logviewer.service
rm -f "$_tmp_unit"

systemctl daemon-reload
# 审计 X-31：改前是 `systemctl enable --now gipfel` —— 对**已在运行**的 unit 这是空操作，
# 重部署后 daphne 仍是旧进程、后端代码改动完全不生效（真机复现：改完 auth 层重跑部署，
# 接口行为一字未变，极易被误判成"修复没写对"）。改为 enable + restart：
# 首次安装时 restart 等价于 start，重部署时强制加载新代码。
# enable 失败（如 unit 仍被 masked）不能直接中止脚本：交给下方 is-active 检查统一诊断报告
systemctl enable gipfel 2>/dev/null || warn "systemctl enable gipfel 失败（unit 可能仍被 masked），详见下方活性检查"
systemctl restart gipfel 2>/dev/null || warn "systemctl restart gipfel 失败，详见下方活性检查"
sleep 2
if ! systemctl is-active --quiet gipfel; then
    warn "gipfel 服务未立即激活，等待 5s 重试检查"
    sleep 5
fi
systemctl is-active --quiet gipfel && ok "gipfel.service 运行中（daphne :8000，/socket.io/）" || \
    { systemctl show -p LoadState,FragmentPath gipfel | while IFS= read -r l; do warn "  $l"; done; \
      journalctl -u gipfel -n 30 --no-pager; err "gipfel 服务启动失败，见上方日志"; }

# C1-a：gunicorn(WSGI) —— 与 daphne 同样必须 restart（enable --now 对已运行 unit 是空操作）。
# 启动顺序上 daphne 先起（它是实时总线的 hub），WSGI 的 forward 即使失败也只丢事件不阻断业务，
# 故这里不做"daphne 必须先就绪"的强校验。
systemctl enable gipfel-wsgi 2>/dev/null || warn "systemctl enable gipfel-wsgi 失败（unit 可能仍被 masked），详见下方活性检查"
systemctl restart gipfel-wsgi 2>/dev/null || warn "systemctl restart gipfel-wsgi 失败，详见下方活性检查"
sleep 2
if ! systemctl is-active --quiet gipfel-wsgi; then
    warn "gipfel-wsgi 服务未立即激活，等待 5s 重试检查"
    sleep 5
fi
systemctl is-active --quiet gipfel-wsgi && ok "gipfel-wsgi.service 运行中（gunicorn :${_WSGI_PORT}，/api/）" || \
    { systemctl show -p LoadState,FragmentPath gipfel-wsgi | while IFS= read -r l; do warn "  $l"; done; \
      journalctl -u gipfel-wsgi -n 30 --no-pager; err "gipfel-wsgi 服务启动失败，见上方日志（常见原因：gunicorn 未安装 / 端口被占用 / GIPFEL_WSGI_PORT 非法）"; }

# 日志查看器（独立站点，nginx 子域 log.<DOMAIN> 代理）
# 同 X-31：enable --now 对已运行 unit 是空操作，必须 restart 才会加载新代码
systemctl enable gipfel-logviewer 2>/dev/null || warn "systemctl enable gipfel-logviewer 失败（unit 可能仍被 masked），详见下方活性检查"
systemctl restart gipfel-logviewer 2>/dev/null || warn "systemctl restart gipfel-logviewer 失败，详见下方活性检查"
sleep 2
if ! systemctl is-active --quiet gipfel-logviewer; then
    warn "gipfel-logviewer 服务未立即激活，等待 5s 重试检查"
    sleep 5
fi
systemctl is-active --quiet gipfel-logviewer && ok "gipfel-logviewer.service 运行中" || \
    { journalctl -u gipfel-logviewer -n 30 --no-pager; err "gipfel-logviewer 服务启动失败，见上方日志"; }

# ---------------- 7. nginx ----------------
if [[ $WITH_NGINX -eq 1 ]]; then
    log "配置 nginx 虚拟主机"
    # 渲染到独立临时文件再落位：PROJECT_ROOT 与 INSTALL_DIR 同目录（原地部署）时，
    # 直接 > 到 deploy/ 下的目标会先清空模板、sed 读到空内容——模板与产物一起作废（与 unit 同款事故）。
    VHOST_OUT="/etc/nginx/sites-available/gipfel.conf"
    _tmp_vhost="$(mktemp /tmp/gipfel.vhost.XXXXXX)"
    LV_PORT="$(_log_viewer_port "$INSTALL_DIR/backend/.env")"
    log "日志查看器公网监听端口：${LV_PORT}（来源：.env LOG_VIEWER_PORT；daphne 内部仍绑 8121）"
    sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
        -e "s|__DOMAIN__|${DOMAIN:-_}|g" \
        -e "s|__LOG_VIEWER_PORT__|${LV_PORT}|g" \
        "$PROJECT_ROOT/deploy/nginx-gipfel.conf" > "$_tmp_vhost"
    # 防御：模板与脚本版本撕裂（同 update-from-github.sh：deploy/nginx-gipfel.conf 是新版
    # 带占位符、但本脚本是旧版没替换逻辑）→ vhost 残留字面量 __LOG_VIEWER_PORT__，nginx -t
    # 报 host not found。检测到残留就 fallback 兜底并强烈告警，让运维对齐版本。
    if grep -q '__LOG_VIEWER_PORT__' "$_tmp_vhost"; then
        warn "vhost 残留 __LOG_VIEWER_PORT__（脚本与模板版本撕裂），fallback 用本脚本内联值 ${LV_PORT} 兜底"
        sed -i "s|__LOG_VIEWER_PORT__|${LV_PORT}|g" "$_tmp_vhost"
        warn "请将 scripts/deploy-linux.sh 与 deploy/nginx-gipfel.conf 同步升级到同一 commit 后再跑"
    fi

    # C1-a：把 upstream gipfel_django 的端口对齐到 .env 的 GIPFEL_WSGI_PORT。
    #   模板里写死 `server 127.0.0.1:8002`（默认拓扑一眼可见、便于回归断言）；
    #   但 unit 侧端口可被 .env 覆盖，若只改 .env 而不动模板，nginx 会一直把 /api/ 打到
    #   没人监听的 8002 → 全站 502，且 nginx -t 通过、毫无线索。这里做渲染期同步。
    if [[ "$_WSGI_PORT" != "8002" ]]; then
        warn "检测到 .env 的 GIPFEL_WSGI_PORT=${_WSGI_PORT} ≠ 模板默认 8002 → 同步改写渲染产物里的 upstream gipfel_django"
        sed -i "s|127.0.0.1:8002|127.0.0.1:${_WSGI_PORT}|g" "$_tmp_vhost"
        # 必须改到且只改到一处（top-level upstream 里那一行；注释里的说明文字同步改掉也无害）
        if ! grep -q "server 127.0.0.1:${_WSGI_PORT} fail_timeout" "$_tmp_vhost"; then
            err "改写 upstream 端口失败：渲染产物里找不到 'server 127.0.0.1:${_WSGI_PORT} fail_timeout'（模板 upstream gipfel_django 可能被改动）"
        fi
    fi
    # 自检：upstream gipfel_django 必须指向 WSGI 端口，upstream gipfel_socketio 必须指向 daphne:8000
    grep -qE "server 127\.0\.0\.1:${_WSGI_PORT} fail_timeout" "$_tmp_vhost" \
        || err "渲染产物里 upstream gipfel_django 未指向 WSGI 端口 ${_WSGI_PORT}，请检查 deploy/nginx-gipfel.conf"
    grep -qE 'server 127\.0\.0\.1:8000 fail_timeout' "$_tmp_vhost" \
        || err "渲染产物里 upstream gipfel_socketio 未指向 daphne 127.0.0.1:8000，请检查 deploy/nginx-gipfel.conf"
    VHOST_FILE="$_tmp_vhost"

    # 日志查看器 server 块二选一（模板含两块，按是否传 --domain 删除另一块）：
    #   有域名 → 保留 log.<DOMAIN> 子域块，删除 8120 端口块；
    #   无域名 → 保留 8120 端口块，删除子域块（server_name log._ 形同失效，干脆移除避免歧义）。
    if [[ -n "$DOMAIN" ]]; then
        sed -i '/# === LOGVIEWER_PORT8120_START ===/,/# === LOGVIEWER_PORT8120_END ===/d' "$VHOST_FILE"
    else
        sed -i '/# === LOGVIEWER_SUBDOMAIN_START ===/,/# === LOGVIEWER_SUBDOMAIN_END ===/d' "$VHOST_FILE"
    fi

    # ---------------- HTTPS：启用 443（Cloudflare Origin Certificate）----------------
    # 模板中的 443 块默认是**注释态**（保证未启用时 nginx -t 恒通过）。本段按 --origin-cert
    # 取消注释并填入证书路径。为什么提供这条路：Let's Encrypt 的 HTTP-01 要为每个 -d 名字
    # 做校验，log.<DOMAIN> 没有 DNS 记录时 certbot 会**整体中止**、443 块一个字节都写不进去
    # （真实的 521 事故根因）；Origin Certificate 由 Cloudflare 直接签发，不依赖 DNS 校验。
    # ⚠️ 走 certbot 的用户**不要**加 --origin-cert：certbot 会自己写 443 块，两者会让同一
    #    server_name 出现两个 443 块（nginx 报 conflicting server name 并只用一个）。
    if [[ "$ORIGIN_CERT" == 1 ]]; then
        if [[ "$WITH_NGINX" != 1 ]]; then
            err "--origin-cert 需要与 --with-nginx 一起使用（否则没有可改写的 vhost）"
        fi
        if [[ -z "$DOMAIN" ]]; then
            err "--origin-cert 需要同时传 --domain（默认用它推导证书文件名 <目录>/<域名>.pem|.key；
     如需自定义路径请用 --ssl-cert / --ssl-key）"
        fi
        # 路径：显式 --ssl-cert/--ssl-key 优先，否则按 <DIR>/<域名>.pem|.key 推导
        [[ -n "$SSL_CERT" ]] || SSL_CERT="${ORIGIN_CERT_DIR}/${DOMAIN}.pem"
        [[ -n "$SSL_KEY"  ]] || SSL_KEY="${ORIGIN_CERT_DIR}/${DOMAIN}.key"
        for _f in "$SSL_CERT" "$SSL_KEY"; do
            if [[ ! -s "$_f" ]]; then
                err "证书文件不存在或为空：$_f
  请先在 Cloudflare 面板「SSL/TLS → 源服务器 → 创建证书」签发，
  Hostnames 填 ${DOMAIN} 与 log.${DOMAIN}（或 *.${DOMAIN}），然后把两个文件放好：
    sudo install -d -m 755 ${ORIGIN_CERT_DIR}
    sudo install -m 644 <下载的 cert.pem> ${ORIGIN_CERT_DIR}/${DOMAIN}.pem
    sudo install -m 600 <下载的 key.pem>  ${ORIGIN_CERT_DIR}/${DOMAIN}.key
  私钥只需 root 可读（nginx master 以 root 读取），故 600 即可。"
            fi
        done
        # ① 主站 443 块：取消整段注释（标记行本身保持注释，便于重复运行）
        sed -i -E "/^# === NGINX_SSL_443_START ===$/,/^# === NGINX_SSL_443_END ===$/ { /^# === NGINX_SSL/! { s/^# //; s/^#$// } }" "$VHOST_FILE"
        # ② 日志查看器子域 443 块：同理。少了它，前端按钮指向的 https://log.<域名>/ 无人应答
        sed -i -E "/^# === NGINX_SSL_443_LOGVIEWER_START ===$/,/^# === NGINX_SSL_443_LOGVIEWER_END ===$/ { /^# === NGINX_SSL/! { s/^# //; s/^#$// } }" "$VHOST_FILE"
        # ②b 日志查看器「域名 + 非标准 TLS 端口」块（--logviewer-tls-port）
        #     为什么单列一块：域名走 Cloudflare 时 CF 只代理固定端口，默认 8120 不在其中，
        #     而 log.<域名> 又可能加不了记录 —— 于是用同一主机名的 CF 受支持端口（8443）。
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            sed -i -E "/^# === LOGVIEWER_TLS_PORT_START ===$/,/^# === LOGVIEWER_TLS_PORT_END ===$/ { /^# === LOGVIEWER_TLS/! { s/^# //; s/^#$// } }" "$VHOST_FILE"
        else
            # 未启用：整段删除，避免留下 __LOG_VIEWER_TLS_PORT__ 占位符导致 nginx -t 失败
            sed -i '/^# === LOGVIEWER_TLS_PORT_START ===$/,/^# === LOGVIEWER_TLS_PORT_END ===$/d' "$VHOST_FILE"
        fi
        # ③ 填入证书路径与端口
        #   ★ 这一步必须**先于下面所有检查**：检查的是「替换完成后的最终产物」。
        #     曾把 `grep 'listen <端口> ssl'` 放在端口替换之前 → 那一刻产物里还是
        #     `listen __LOG_VIEWER_TLS_PORT__ ssl;`，grep 必然失败并误报「模板被改动」。
        sed -i -e "s|__SSL_CERT__|${SSL_CERT}|g" -e "s|__SSL_KEY__|${SSL_KEY}|g" "$VHOST_FILE"
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            sed -i "s|__LOG_VIEWER_TLS_PORT__|${LOGVIEWER_TLS_PORT}|g" "$VHOST_FILE"
        fi
        # ④ 防御：残留占位符说明模板与脚本版本撕裂，此时 nginx -t 必然失败，提前给可读错误
        if grep -q '__SSL_CERT__\|__SSL_KEY__\|__LOG_VIEWER_TLS_PORT__' "$VHOST_FILE"; then
            err "vhost 仍残留占位符（__SSL_CERT__/__SSL_KEY__/__LOG_VIEWER_TLS_PORT__），模板与脚本版本不一致；请把 deploy/nginx-gipfel.conf 与 scripts/ 同步到同一 commit 后重跑"
        fi
        # ⑤ 自检：确认各 443/TLS 块真的进了产物（在占位符全部替换之后判断）
        if ! grep -q 'listen 443 ssl' "$VHOST_FILE"; then
            err "已在 --origin-cert 模式下渲染，但产物里没有 listen 443 ssl —— 模板的 SSL 标记可能被改动，请检查 deploy/nginx-gipfel.conf"
        fi
        if [[ -n "$LOGVIEWER_TLS_PORT" ]] \
           && ! grep -qE "^[[:space:]]*listen[[:space:]]+${LOGVIEWER_TLS_PORT}[[:space:]]+ssl;" "$VHOST_FILE"; then
            err "已请求 --logviewer-tls-port ${LOGVIEWER_TLS_PORT}，但产物里没有 'listen ${LOGVIEWER_TLS_PORT} ssl;' —— 模板的 LOGVIEWER_TLS_PORT 标记可能被改动，请检查 deploy/nginx-gipfel.conf"
        fi
        # ⑥ 自检：区域内若混入「散文注释」，取消一层注释后会变成非法指令。
        #   曾真实发生：unknown directive "日志查看器子域的" —— nginx 的报错不会说明
        #   是模板问题，运维很难定位，故提前用可读错误拦住。
        #   注意必须先 `sed 's/#.*$//'` 剥掉**行内注释**再判断：模板里存在合法行内注释
        #   （如 `proxy_read_timeout 86400s;   # 长连接`），只看行首会把它误判成散文。
        _bad_prose="$(LC_ALL=C sed 's/#.*$//' "$VHOST_FILE" 2>/dev/null \
                      | LC_ALL=C grep -n '[^ -~]' | head -3 || true)"
        if [[ -n "$_bad_prose" ]]; then
            err "渲染后的 vhost 出现「生效的非 ASCII 行」——SSL 标记区域内混入了散文注释（或行内注释前的指令含非 ASCII）：
${_bad_prose}
  NGINX_SSL_443* 区域内只允许放【注释形式的 nginx 配置】；说明文字必须写在标记行之外。
  区域内确需写注释时用两层井号（\`#     # 说明\`），取消一层后仍是注释。"
        fi
        ok "已启用 HTTPS：证书 ${SSL_CERT}；服务 ${DOMAIN} 与 log.${DOMAIN}（443）"
    fi

    # ★ 防「静默摧毁 HTTPS」+ 留可回滚副本。
    #   下一行会用模板产物**整体覆盖** vhost；而 certbot 的 nginx 插件是把 443 块
    #   **直接写进同一个文件**的。若现有 vhost 已含生效的 443，而本次没有 --origin-cert
    #   去重新生成它，覆盖后 443 就消失、HTTPS 静默失效（全程无任何报错）。
    if [[ -f /etc/nginx/sites-available/gipfel.conf ]]; then
        cp -f /etc/nginx/sites-available/gipfel.conf \
              "/etc/nginx/sites-available/gipfel.conf.bak-$(date +%F_%H%M%S)" 2>/dev/null || true
        if [[ "$ORIGIN_CERT" != 1 ]] \
           && grep -qE '^[[:space:]]*listen[[:space:]]+443' /etc/nginx/sites-available/gipfel.conf; then
            err "现有 vhost 已配置 443，而本次未传 --origin-cert——覆盖会让 HTTPS 配置消失（且不报错）。
    请二选一：
      A) 改由脚本管理 HTTPS：把证书放到 ${ORIGIN_CERT_DIR}/<域名>.pem|.key，加 --origin-cert 重跑；
      B) 继续由 certbot 管理：本次**不要加 --with-nginx**（只升级代码与服务），
         或先把 443 块移到 /etc/nginx/snippets/ 下再用 include 引入。
    旧 vhost 已备份为 /etc/nginx/sites-available/gipfel.conf.bak-<时间戳>，可随时还原。"
        fi
        # 同类防护（日志查看器非标准 TLS 端口）：若现有 vhost 已在 8443 之类的端口上提供
        # TLS，而本次未传 --logviewer-tls-port，覆盖后该块会消失 —— 前端按钮随即指向空气。
        # 注意必须带 || true：管道里首个 grep 无匹配会退出 1，pipefail 下会让赋值失败并终止脚本。
        _stale_tls_ports="$(grep -oE '^[[:space:]]*listen[[:space:]]+[0-9]+[[:space:]]+ssl' \
                              /etc/nginx/sites-available/gipfel.conf 2>/dev/null \
                            | grep -oE '[0-9]+' | grep -v '^443$' | sort -u | tr '\n' ' ' || true)"
        if [[ -z "$LOGVIEWER_TLS_PORT" && -n "$_stale_tls_ports" ]]; then
            err "现有 vhost 已在非标准端口上提供 TLS（端口：${_stale_tls_ports}），而本次未传 --logviewer-tls-port——
    覆盖后这些端口块会消失，日志查看器可能因此打不开（且不报错）。
    若确实在用，请带上 --logviewer-tls-port <端口> 重跑；
    若有意移除，请手动确认后再执行（旧 vhost 已备份）。"
        fi
    fi

    cp -f "$VHOST_FILE" /etc/nginx/sites-available/gipfel.conf
    rm -f "$_tmp_vhost"

    # 按 nginx.conf 实际 include 风格放置 gipfel 配置（兼容 Debian 的 sites-enabled 与
    # 仅 include conf.d 的精简镜像），避免放错位置导致配置根本不被加载、或两处重复 server 块。
    # 同时清理另一处的残留 gipfel 软链/文件，防止重复。
    if grep -q 'sites-enabled' /etc/nginx/nginx.conf 2>/dev/null; then
        ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/gipfel.conf
        rm -f /etc/nginx/conf.d/gipfel.conf
        ok "已启用 gipfel nginx 虚拟主机（sites-enabled/gipfel.conf）"
    elif grep -q 'conf.d' /etc/nginx/nginx.conf 2>/dev/null; then
        cp -f /etc/nginx/sites-available/gipfel.conf /etc/nginx/conf.d/gipfel.conf
        rm -f /etc/nginx/sites-enabled/gipfel.conf
        ok "已启用 gipfel nginx 虚拟主机（conf.d/gipfel.conf，因 nginx.conf 仅 include conf.d）"
    else
        # 兜底：两处都放（绝大多数默认配置两者至少含其一；若都无 include 则属异常环境）
        ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/gipfel.conf
        cp -f /etc/nginx/sites-available/gipfel.conf /etc/nginx/conf.d/gipfel.conf
        ok "已启用 gipfel nginx 虚拟主机（sites-enabled + conf.d 均放置，兜底）"
    fi

    # 禁用 nginx 自带默认欢迎页（避免与 gipfel 主站点 server_name _ 在 80 端口冲突 → 显示 "Welcome to nginx"）
    # 注意：nginx 按 sites-enabled/* 通配包含，「改名 default.disabled」无法禁用（仍被 * 匹配），必须删除才能真正禁用。
    # 枚举所有已知变体：Debian/Ubuntu 的 sites-enabled/default（或旧脚本改名留下的 default.disabled）、
    # RHEL 系的 conf.d/default.conf。漏删任一个都会让 80 端口被默认站点抢走。
    for f in /etc/nginx/sites-enabled/default \
             /etc/nginx/sites-enabled/default.disabled \
             /etc/nginx/sites-enabled/default.conf \
             /etc/nginx/conf.d/default.conf; do
        if [[ -e "$f" ]]; then
            rm -f "$f"
            log "已移除 nginx 默认站点配置：$f"
        fi
    done

    nginx -t || err "nginx -t 失败，请修正"

    # 审计 X-24：`nginx -t` 只校验语法，不检测端口冲突。这里在 reload/start 之前看一眼
    # ${LV_PORT} 的占用者是不是 nginx —— 若是别的进程，reload 会因 bind 失败而保持旧配置，
    # 而旧配置下站点/日志查看器都不可用。占用者是 nginx 时属正常重部署，不报问题。
    if command -v ss >/dev/null 2>&1; then
        _lv_owner="$(ss -ltnpH "sport = :${LV_PORT}" 2>/dev/null | head -1 || true)"
        if [[ -n "$_lv_owner" ]] && ! printf '%s' "$_lv_owner" | grep -q 'nginx'; then
            problem "端口 ${LV_PORT} 已被非 nginx 进程占用：${_lv_owner}；请改 .env 的 LOG_VIEWER_PORT 或先释放该端口"
        fi
    fi

    # 全新服务器 nginx 可能尚未启动，reload 对未运行服务会失败；按状态选择 start / reload
    systemctl enable nginx 2>/dev/null || true
    if systemctl is-active --quiet nginx; then
        if systemctl reload nginx; then
            ok "nginx 配置已 reload"
        else
            problem "nginx reload 失败：新配置未生效，站点仍在跑旧配置"
        fi
    else
        if systemctl start nginx; then
            ok "nginx 已启动"
        else
            problem "nginx 启动失败（常见原因：${LV_PORT} 或 80 端口被占用）"
        fi
    fi

    # 审计 X-24：reload/start 之后立刻做功能探针，并把结果并入问题计数，
    # 而不是只打印一行 `ok` 让 CI 误判成功。
    if ! systemctl is-active --quiet nginx; then
        problem "nginx 未处于 active 状态（systemctl status nginx 查看原因）"
    fi
    if command -v curl >/dev/null 2>&1; then
        _lv_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${LV_PORT}/" 2>/dev/null || true)"
        case "$_lv_code" in
            2*|3*) ok "日志查看器站点探针通过（HTTP ${_lv_code}）" ;;
            "")    problem "日志查看器站点探针无响应：http://127.0.0.1:${LV_PORT}/" ;;
            *)     problem "日志查看器站点探针返回 HTTP ${_lv_code}（期望 2xx/3xx）" ;;
        esac
        _api_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/api/health" 2>/dev/null || true)"
        case "$_api_code" in
            2*|3*) ok "后端 /api/health 探针通过（HTTP ${_api_code}）" ;;
            "")    problem "后端 /api/health 探针无响应（nginx 或 gipfel 未起来）" ;;
            *)     problem "后端 /api/health 探针返回 HTTP ${_api_code}（期望 2xx/3xx）" ;;
        esac
        # C1-a：再**直连两个后端端口**分别探活。经 nginx 的 /api/health 只能证明"某个后端活着"；
        # 分离部署最常见的故障就是只起了一个 unit（/api/ 正常但实时全掉，或反之），
        # 必须按端口分别确认：WSGI(gunicorn) :8002/api/health 与 daphne :8000/socket.io/ 握手。
        if ! split_backend_health "$_WSGI_PORT" "${GIPFEL_DAPHNE_PORT:-8000}"; then
            problem "WSGI/daphne 分层健康检查未全部通过（见上方 WARN 行）：两个进程都必须在跑（/api/ 走 WSGI，/socket.io/ 走 daphne）"
        fi
    else
        warn "未安装 curl，跳过部署后的功能探针"
    fi

    # 验证：80 端口不应再返回 nginx 默认欢迎页；若后端未起则给出 502 排查提示而非误判
    sleep 1
    if command -v curl >/dev/null 2>&1; then
        _body="$(curl -s --max-time 5 http://127.0.0.1/ 2>/dev/null || true)"
        if printf '%s' "$_body" | grep -qi 'Welcome to nginx'; then
            problem "80 端口仍返回 nginx 默认欢迎页：默认站点未被完全禁用。请检查 /etc/nginx/nginx.conf 是否内联了默认 server 块，或仍有其它 sites-enabled/* 配置冲突"
        elif ! systemctl is-active --quiet gipfel; then
            problem "nginx 已正确接管 80 端口，但后端 gipfel 服务未运行，访问将出现 502；请执行：sudo systemctl restart gipfel"
        else
            ok "80 端口验证通过：gipfel 站点已生效（非默认欢迎页）"
        fi
    fi

    # 80/443 放行：nginx 接管 80 是必然的；443 在启用 HTTPS 后必需。
    #   本脚本**不自动申请证书**（未确认 DNS / 安全组就中途跑 certbot，失败时会把
    #   nginx 留在半配置状态），所以这里先把 443 一并放行，让后续
    #   `certbot --nginx ...` 一步到位即生效、无需再回来改防火墙。
    #   ⚠️ 放行的是「源站」。若域名前面挂了 Cloudflare 等 CDN，CDN 回源同样要能连上
    #      443，否则 CDN 侧报 521（Web server is down，含义是连不上源站）。
    #      详见 deploy/README.md 的「Cloudflare / CDN 前置」一节。
    if command -v ufw >/dev/null 2>&1; then
        ufw allow 80/tcp  >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            ufw allow "${LOGVIEWER_TLS_PORT}/tcp" >/dev/null 2>&1 || true
            ok "已放行防火墙 80/443/${LOGVIEWER_TLS_PORT} 端口（若 ufw 未启用则该规则暂未生效）"
        else
            ok "已放行防火墙 80/443 端口（若 ufw 未启用则该规则暂未生效）"
        fi
    else
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            warn "请确认云/系统防火墙放行 TCP 80、443 与 ${LOGVIEWER_TLS_PORT}，否则 HTTPS / 日志查看器不可达。"
        else
            warn "请确认云/系统防火墙放行 TCP 80 与 443，否则 HTTPS 不可达（经 CDN 回源时报 521）。"
        fi
    fi

    if [[ -n "$DOMAIN" ]]; then
        if [[ "$ORIGIN_CERT" == 1 ]]; then
            # 已用 Origin Certificate 启用 443：不要再引导去跑 certbot（会重复 443 块）
            ok "HTTPS 已按 Cloudflare Origin Certificate 启用（无需 certbot，也请勿再对其执行 --nginx 签发）"
            warn "仍需确认：① Cloudflare SSL/TLS 模式为 Full (strict)；② log.$DOMAIN 的 DNS 记录已存在"
            warn "  （Origin Certificate 不校验 DNS，所以证书能装上，但访客解析不到 log.$DOMAIN 依然打不开日志查看器）。"
            warn "  证书有效期最长 15 年，到期前在 CF 面板重新签发并替换两个文件后重跑本脚本即可。"
        elif command -v certbot >/dev/null 2>&1; then
            warn "已安装 certbot，可手动执行：certbot --nginx -d $DOMAIN --non-interactive --redirect"
        else
            warn "如需 HTTPS：apt-get install -y certbot python3-certbot-nginx && certbot --nginx -d $DOMAIN --redirect"
        fi
        if [[ "$ORIGIN_CERT" != 1 ]]; then
            warn "另需：将 log.$DOMAIN 的 DNS A 记录指向本服务器（日志查看器子域代理前置条件）。"
            warn "★ 先只签主域。加 -d log.$DOMAIN 时若该子域无 DNS 记录，certbot 会整体中止、443 块写不进去。"
            warn "  若必须一次覆盖两个名字，改用 Cloudflare Origin Certificate：--origin-cert（不依赖 DNS 校验）。"
            warn "Cloudflare 代理场景：SSL/TLS 模式须为 Full (strict)，切勿用 Flexible（与 --redirect 叠加会变成重定向循环）。见 deploy/README.md。"
        fi
    else
        # 无域名：日志查看器经 ${LV_PORT} 端口暴露公网（取自 .env LOG_VIEWER_PORT），
        # 需放行防火墙
        if command -v ufw >/dev/null 2>&1; then
            # 先清理旧 8120 规则（防改端口后旧规则残留），再加新规则
            ufw delete allow 8120/tcp >/dev/null 2>&1 || true
            ufw allow "${LV_PORT}/tcp" >/dev/null 2>&1 || true
            ok "已放行防火墙 ${LV_PORT} 端口（ufw 规则已添加；旧 8120 规则已清理；若 ufw 未启用则该规则暂未生效）"
        else
            warn "无域名部署：请确认云/系统防火墙放行 TCP ${LV_PORT}，否则 http://<IP>:${LV_PORT}/ 不可达。"
        fi
    fi
fi

# ---------------- 收尾 ----------------
echo
# 审计 X-24：把"降级继续"的分支汇总成退出码，而不是永远 `ok "部署完成！"` + exit 0。
if [[ "$DEPLOY_PROBLEMS" -gt 0 ]]; then
    warn "部署结束：有 ${DEPLOY_PROBLEMS} 项检查未通过（详见上方 [WARN] 行）"
    if [[ "$ALLOW_PARTIAL" == "1" ]]; then
        warn "--allow-partial 已指定：仍以退出码 0 结束（CI/自动化请勿这样用）"
        ok "部署完成（有 ${DEPLOY_PROBLEMS} 项待人工确认）！"
    else
        err "部署未完全成功：请按上方提示处理后重跑；确需接受当前状态请加 --allow-partial"
    fi
else
    ok "部署完成！"
fi
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
# 读取管理员密码并醒目输出（★ 必须容忍无匹配：首次部署 .env 尚未写入该行时 grep 退出 1，
#   pipefail 下会让这行赋值失败 → set -e 在脚本最后一步静默终止，看不到任何凭据输出）
# 审计 X-22（master 合并后重做 → 现行口径）：口令只在 **stdout 为交互终端** 时回显；
#   管道 / CI / 重定向（`| tee deploy.log` 等）下绝不打印，避免管理员明文进入日志。
#   与上方「首次部署生成」处保持同一守卫条件（`-t 1`）。
_SEED_PW="$(grep -E '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' | sed -E "s/^['\"]//; s/['\"]$//" || true)"
if [[ -n "$_SEED_PW" ]]; then
    if [[ -t 1 ]]; then
        echo "  👤 管理员账号：admin"
        echo "  🔑 管理员密码：${_SEED_PW}"
        echo "     （首次登录会强制修改；口令仅在本终端显示，未写入任何日志）"
    else
        echo "  👤 管理员账号：admin"
        echo "  🔑 管理员密码：见 ${INSTALL_DIR}/backend/.env 的 SEED_ADMIN_PASSWORD"
        if [[ "$PRINT_SEED_PASSWORD" == "1" ]]; then
            warn "--print-seed-password 已给出，但 stdout 不是终端（管道/CI）—— 为避免口令进入日志，此处不打印。"
        fi
    fi
else
    echo "  🔑 管理员密码：见 ${INSTALL_DIR}/backend/.env 的 SEED_ADMIN_PASSWORD"
    echo "     查看命令：sudo grep '^SEED_ADMIN_PASSWORD=' ${INSTALL_DIR}/backend/.env"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo

# 公网 IP 提示：优先采用用户显式 --public-ip；否则用与首跑一致的 _probe_public_ip() 探测
# （含 timeout 硬包裹，无外网/异常 DNS 时不会卡在结尾）。探测仍失败才提示手动查询。
# 注意：此处【不可】重置 PUBLIC_IP，否则会覆盖用户传入的值，导致结尾误报「未获取到 IP」。
if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP="$(_probe_public_ip)" || true
fi
if [[ -z "$PUBLIC_IP" ]]; then
    PUBLIC_IP_HINT="（未能自动获取公网 IP，可访问 https://ifconfig.me 或云控制台查看）"
    PUBLIC_IP="<公网IP>"
elif [[ $PUBLIC_IP_SET -eq 1 ]]; then
    PUBLIC_IP_HINT="（使用你通过 --public-ip 指定的公网 IP）"
else
    PUBLIC_IP_HINT=""
fi

echo "  目录：        $INSTALL_DIR"
echo "  后端状态：    systemctl status gipfel        # daphne :8000（/socket.io/，实时总线 hub）"
echo "                systemctl status gipfel-wsgi   # gunicorn :${_WSGI_PORT}（/api/、/admin/，WSGI）"
if [[ $WITH_NGINX -eq 1 ]]; then
    SERVER_IP="$(hostname -I | awk '{print $1}' || true)"
    if [[ -n "$DOMAIN" ]]; then
        if [[ "$ORIGIN_CERT" == 1 ]]; then
            echo "  网站(公网)：   https://${DOMAIN}/"
        else
            echo "  网站(公网)：   http://${DOMAIN}/（启用 HTTPS 见 deploy/README.md）"
        fi
        echo "  网站(内网)：   http://${SERVER_IP}/"
    else
        echo "  网站(内网)：   http://${SERVER_IP}/"
        echo "  网站(公网)：   http://${PUBLIC_IP}/"
        echo "  日志查看器(内)： http://${SERVER_IP}:${LV_PORT}/"
        echo "  日志查看器(公)： http://${PUBLIC_IP}:${LV_PORT}/"
        echo "                 （需放行防火墙 ${LV_PORT}；前端「系统设置 → 日志查看器」按钮跳转）"
    fi
    echo "  Nginx 状态：  systemctl status nginx"
fi
echo "  日志：        journalctl -u gipfel -f   /   journalctl -u gipfel-wsgi -f   /   tail -F $INSTALL_DIR/backend/logs/app.log"

# ============================================================
# 部署自检：把「线上实际生效的状态」直接打出来。
# 目的——**不需要人工去查 .env / 进程环境 / 监听端口**。任何一项不对，这里就说清是什么、怎么修。
# 起因：多次出现「改了配置但没生效」的排查拉锯（.env 遗留值、改完没重启、端口没监听……
#       每一样都得人工逐个去猜）。脚本既然知道期望值，就该自己核对并报告。
# 注意：本段只读不写；任何一项失败都**不中止**（部署主体已完成，自检只做告知）。
# ============================================================
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  部署自检（只读，不影响上面已完成的部署）"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_SELFCHECK_WARN=0

# 1) 后端进程**实际读到**的日志查看器地址（唯一算数的答案；进程环境来自 systemd EnvironmentFile）
if command -v systemctl >/dev/null 2>&1; then
    _pid="$(systemctl show -p MainPID gipfel 2>/dev/null | cut -d= -f2)"
    if [[ -n "$_pid" && "$_pid" != "0" && -r "/proc/$_pid/environ" ]]; then
        _eff="$(tr '\0' '\n' < "/proc/$_pid/environ" 2>/dev/null | grep -E '^LOG_VIEWER_PUBLIC_URL=' | tail -1 | cut -d= -f2- || true)"
        if [[ -n "$_eff" ]]; then
            echo "  [OK]   后端生效的日志查看器地址：${_eff}"
            echo "         → 前端「系统设置 → 日志查看器」按钮应跳转到该地址"
        else
            echo "  [WARN] 后端进程环境里没有 LOG_VIEWER_PUBLIC_URL"
            echo "         → 将按请求 Host 推导；若 Host 是 IP 会得到 http://<IP>:${LV_PORT}/（CF 代理下打不开）"
            echo "         修：重跑本脚本（会自动写该变量），或检查 .env 是否被手工改回"
            _SELFCHECK_WARN=1
        fi
    else
        echo "  [WARN] 读不到 gipfel 进程环境（未运行或无权读取）→ systemctl status gipfel"
        _SELFCHECK_WARN=1
    fi
fi

# 1b) C1-a：两个后端进程**实际读到**的实时总线角色 —— 用来确认 unit 里的 Environment=
#     真的生效（或 .env 有意覆盖成了 redis）。角色配错的后果是"实时广播静默失效"（无报错）。
if command -v systemctl >/dev/null 2>&1; then
    for _svc in gipfel gipfel-wsgi; do
        _bus_pid="$(systemctl show -p MainPID "$_svc" 2>/dev/null | cut -d= -f2)"
        if [[ -n "$_bus_pid" && "$_bus_pid" != "0" && -r "/proc/$_bus_pid/environ" ]]; then
            _bus="$(tr '\0' '\n' < "/proc/$_bus_pid/environ" 2>/dev/null | grep -E '^REALTIME_BUS=' | tail -1 | cut -d= -f2- || true)"
            echo "  [OK]   ${_svc} 生效的 REALTIME_BUS=${_bus:-（未设置 → 应用按 auto 判定）}"
            case "${_svc}:${_bus}" in
                gipfel:hub|gipfel-wsgi:forward|gipfel:redis|gipfel-wsgi:redis) : ;;
                *) echo "  [WARN] ${_svc} 的 REALTIME_BUS 期望 hub（daphne）/ forward（WSGI），或两端一致的 redis；实际 '${_bus}'"
                   echo "         修：检查 /etc/systemd/system/${_svc}.service 的 Environment= 与 backend/.env 是否互相覆盖；改完 systemctl daemon-reload && systemctl restart gipfel gipfel-wsgi"
                   _SELFCHECK_WARN=1 ;;
            esac
        else
            echo "  [WARN] 读不到 ${_svc} 进程环境（未运行或无权读取）→ systemctl status ${_svc}"
            _SELFCHECK_WARN=1
        fi
    done
fi

# 2) 监听端口：80/443 必有；8443 视形态；8121 是日志查看器内部端口；
#    C1-a 之后还必须看到 **127.0.0.1:8000（daphne）与 127.0.0.1:<WSGI_PORT>（gunicorn）** ——
#    这两个才是分离拓扑的本体，缺任一即说明对应 unit 没起来。
if command -v ss >/dev/null 2>&1; then
    echo "  监听端口："
    ss -lntp 2>/dev/null | grep -E ":(80|443|8000|${_WSGI_PORT}|8121|8443)\b" | sed 's/^/    /' || echo "    （未匹配到 80/443/8000/${_WSGI_PORT}/8121/8443）"
    if ! ss -lnt 2>/dev/null | grep -qE ":${_WSGI_PORT}\b"; then
        echo "  [WARN] WSGI 端口 ${_WSGI_PORT} 没有在监听 → gunicorn 没起来：systemctl status gipfel-wsgi"
        _SELFCHECK_WARN=1
    fi
    if ! ss -lnt 2>/dev/null | grep -qE ':8000\b'; then
        echo "  [WARN] daphne 端口 8000 没有在监听 → /socket.io/ 会 502：systemctl status gipfel"
        _SELFCHECK_WARN=1
    fi
    if [[ -n "$LOGVIEWER_TLS_PORT" ]] && ! ss -lnt 2>/dev/null | grep -qE ":${LOGVIEWER_TLS_PORT}\b"; then
        echo "  [WARN] 已要求日志查看器监听 ${LOGVIEWER_TLS_PORT}，但它没有在监听"
        echo "         → 多半是 nginx 未 reload 或缺少该 server 块：sudo nginx -t && sudo systemctl reload nginx"
        _SELFCHECK_WARN=1
    fi
fi

# 2b) C1-a：直连两个后端端口分别探活（唯一实现见 scripts/lib/deploy-common.sh）。
#     不要只依赖经 nginx 的 /api/health：它区分不出"两个进程都活着"与"只起了一个"。
if ! split_backend_health "$_WSGI_PORT" "${GIPFEL_DAPHNE_PORT:-8000}"; then
    echo "  [WARN] 上方 WSGI/daphne 分层健康检查有失败项：/api/ 走 WSGI、/socket.io/ 走 daphne，两个都必须 active"
    _SELFCHECK_WARN=1
fi

# 3) 日志查看器自身健康（绕开 nginx，直连内部端口）
if curl -sS --max-time 5 http://127.0.0.1:8121/api/health >/dev/null 2>&1; then
    echo "  [OK]   日志查看器（127.0.0.1:8121）健康检查通过"
else
    echo "  [WARN] 日志查看器 127.0.0.1:8121 健康检查失败 → systemctl status gipfel-logviewer"
    _SELFCHECK_WARN=1
fi

# 4) 主机名白名单兜底核对（日志查看器 400 的经典成因）
if [[ -n "$DOMAIN" && -f "$INSTALL_DIR/backend/.env" ]]; then
    if grep -E '^DJANGO_ALLOWED_HOSTS=' "$INSTALL_DIR/backend/.env" | grep -qE "(^|,)${DOMAIN}(,|$)"; then
        echo "  [OK]   DJANGO_ALLOWED_HOSTS 含 ${DOMAIN}"
    else
        echo "  [WARN] DJANGO_ALLOWED_HOSTS 不含 ${DOMAIN} → 经域名访问会 400"
        _SELFCHECK_WARN=1
    fi
fi

if [[ $_SELFCHECK_WARN -eq 0 ]]; then
    echo "  自检结论：未发现问题。"
else
    echo "  自检结论：有 WARN 项，按上面每条的「修：」处理，然后重跑本脚本复核。"
fi
echo
