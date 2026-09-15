#!/usr/bin/env bash
# ============================================================
# Gipfel Business Competition Manager — 从 GitHub 拉取最新并更新（升级脚本）
# 适用：两种方式更新已部署的实例 ——
#   ① 部署目录 /opt/gipfel 本身是 git clone（首次用 git clone 拉起）→ 本脚本模式 A 原地 pull；
#   ② 沿用 deploy-linux.sh 的「本地 checkout → rsync 到 /opt/gipfel」模型 → 本脚本模式 B（--source-dir 指向该 checkout）。
#   首次部署请先用 deploy-linux.sh 或 git clone；本脚本专注「拉取最新 + 备份 + 迁移 + 重启」的升级动作。
# 用法：
#   sudo bash scripts/update-from-github.sh
#   sudo bash scripts/update-from-github.sh --install-dir /opt/gipfel
#   sudo bash scripts/update-from-github.sh --repo https://github.com/owner/repo.git        # 首次克隆
#   sudo bash scripts/update-from-github.sh --source-dir /path/to/checkout                  # 从本地 checkout 同步
#   sudo bash scripts/update-from-github.sh --with-nginx --domain comp.example.com           # 同步刷新并 reload nginx
#
# 代码来源（三选一，自动判定）：
#   模式 A  部署目录本身是 git clone        → 原地 git pull（推荐：服务器上 git clone 后日常更新）
#   模式 B  提供 --source-dir（本地 checkout）→ 该目录 git pull 后 rsync 同步到 INSTALL_DIR（同 deploy-linux.sh 模型）
#   模式 C  提供 --repo 且 INSTALL_DIR 为空  → git clone 到 INSTALL_DIR（首次拉起）
#
# 步骤：
#   1. 拉取最新代码（按上述模式）
#   2. 备份 db.sqlite3 + uploads + .env 到 _backup/<时间戳>（安全副本，migrate 前）
#   3. 更新后端：pip install -r requirements.txt → migrate → collectstatic
#   4. 更新前端：npm ci && npm run build → frontend-dist
#   5. chown 归属运行用户 gipfel，.env 权限 600
#   6. 刷新并重启 systemd 服务：把最新 deploy/*.service 重新落到 /etc/systemd/system/（替换 __INSTALL_DIR__，
#      daemon-reload），再 systemctl restart gipfel（+ gipfel-logviewer 日志查看器）。这一步保证仓库对服务单元
#      的改动（如 8121 端口修复）能传播到 live 单元，与 deploy-linux.sh 第 6 步一致。
#   7. [可选] --with-nginx：用最新 deploy/nginx-gipfel.conf 重新生成 vhost（按 --domain 保留对应日志查看器块）
#      并 reload（含 80 端口校验、默认站点禁用、防火墙放行）
#
# 注意：
#   - 数据文件（db.sqlite3 / uploads / .env / logs / staticfiles / .venv / node_modules / frontend-dist）
#     均已被 .gitignore 忽略（模式 B 的 rsync 也显式排除），更新不会触碰它们，业务数据在升级间自动保留。
#   - 若工作区有未提交的源码改动，git pull --ff-only 会失败并中止（避免覆盖），请先处理或 stash。
# ============================================================
set -euo pipefail

# ---------------- 失败时必须说清「停在哪一行」 ----------------
# 背景（真实事故）：脚本在打印完「文件归属已切换为 gipfel」之后**无任何提示地结束**，
# 运维既不知道停在哪一步，也不知道该手工补哪一步。根因是 `set -e` 本身**不输出任何东西**——
# 任何非零返回都只是安静地 exit。故在此装 ERR trap，把「哪一行、哪条命令」打出来。
#
# 触发条件与 set -e 一致：if / while 条件、&& / || 列表、! 取反**不会**触发。
# 注意本 trap 不继承进函数体（未开 set -E，避免改变控制流的风险），
# 因此它覆盖的是顶层命令——正是本次事故（顶层 grep 无匹配）所在的位置。
__on_err() {
    local rc="$1" line="$2" cmd="$3"
    {
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "[FAIL] 脚本在第 ${line} 行终止（退出码 ${rc}）"
        echo "       命令：${cmd}"
        echo ""
        echo "       已完成步骤的成果保留生效（本脚本设计为可重复执行）。"
        echo "       若是 grep「无匹配」导致：属脚本缺陷（该处应容忍无匹配），请把上面两行反馈。"
        echo "       定位后可直接重跑本脚本补齐剩余步骤。"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    } >&2
    exit "$rc"
}
trap '__on_err $? "$LINENO" "$BASH_COMMAND"' ERR

# ---------------- 参数解析 ----------------
DOMAIN=""
INSTALL_DIR=""
SOURCE_DIR=""
REPO=""
WITH_NGINX=0
PUBLIC_IP=""   # 显式指定公网 IP（无域名纯 IP 部署日志查看器用）；非空则跳过自动探测
# HTTPS via Cloudflare Origin Certificate（--origin-cert；与 deploy-linux.sh 同一套语义）
ORIGIN_CERT=0
ORIGIN_CERT_DIR="/etc/ssl/cloudflare"
SSL_CERT=""
SSL_KEY=""
# 日志查看器：域名 + 非标准 TLS 端口（Cloudflare 代理自定义端口形态）
LOGVIEWER_TLS_PORT=""
LOGVIEWER_TLS_PORT_SET=0   # 用户是否显式指定过（--logviewer-tls-port / --no-logviewer-tls）

usage() {
    cat <<EOF
Usage: $0 [options]
  --install-dir PATH       git clone 所在目录，默认取脚本上级目录（即 clone 根 /opt/gipfel）
  --source-dir PATH        本地源码 checkout；git pull 后 rsync 同步到 INSTALL_DIR（兼容 deploy-linux.sh 模型）
  --repo URL               仓库地址；当 INSTALL_DIR 非 git 仓库且目录为空时用于克隆
  --domain DOMAIN          ★ 公网域名。**域名部署请每次都传**，它不只影响 nginx：
                           ① --with-nginx 时重写 nginx server_name；
                           ② 自愈 .env 的 DJANGO_ALLOWED_HOSTS（追加 <域名> 与 log.<域名>，
                              后者是日志查看器能通过 Host 白名单的前提）；
                           ③ 决定日志查看器走 log.<域名> 子域形态而非 <IP>:8120。
                           漏传且 --with-nginx 时仍会按「无域名」重写 vhost，属错误配置。
  --public-ip IP           公网 IP（无 --domain 部署时日志查看器使用 http://<IP>:8120/）；
                          显式传入可跳过自动探测，确保受限网络下也能纠正内网 IP 或补全缺失行
  --with-nginx             更新后重新生成 nginx 虚拟主机并 reload
  --origin-cert            启用 HTTPS（Cloudflare Origin Certificate；需 --with-nginx 与 --domain）。
                           脚本取消 vhost 里 443 模板注释并填入证书路径，然后 nginx -t + reload。
                           证书默认取 <--origin-cert-dir>/<域名>.pem 与 .key
  --origin-cert-dir PATH   证书目录，默认 /etc/ssl/cloudflare
  --ssl-cert PATH          显式指定证书文件（覆盖按域名推导）
  --ssl-key PATH           显式指定私钥文件（覆盖按域名推导）
  --logviewer-tls-port PORT 日志查看器的 HTTPS 端口。**--origin-cert 时默认 8443**，通常无需手传。
                           CF 只代理固定端口（HTTPS 443/2053/2083/2087/2096/8443），而默认的
                           8120 不在其中，故 http://<域名>:8120/ 永远连不上。
                           脚本会把 LOG_VIEWER_PUBLIC_URL 写成 https://<域名>:PORT/
                           另需在云安全组入方向放行该 TCP 端口。
  --no-logviewer-tls        关闭上述端口块（用于改用 log.<域名> 子域形态的部署）
  -h, --help               显示本帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --install-dir)      INSTALL_DIR="$2"; shift 2 ;;
        --source-dir)       SOURCE_DIR="$2"; shift 2 ;;
        --repo)             REPO="$2"; shift 2 ;;
        --domain)           DOMAIN="$2"; shift 2 ;;
        --public-ip)        PUBLIC_IP="$2"; shift 2 ;;
        --with-nginx)       WITH_NGINX=1; shift ;;
        --allow-stale-code) ALLOW_STALE_CODE=1; shift ;;
        --origin-cert)      ORIGIN_CERT=1; shift ;;
        --origin-cert-dir)  ORIGIN_CERT_DIR="$2"; ORIGIN_CERT=1; shift 2 ;;
        --ssl-cert)         SSL_CERT="$2"; ORIGIN_CERT=1; shift 2 ;;
        --ssl-key)          SSL_KEY="$2"; ORIGIN_CERT=1; shift 2 ;;
        --logviewer-tls-port) LOGVIEWER_TLS_PORT="$2"; LOGVIEWER_TLS_PORT_SET=1; shift 2 ;;
        --no-logviewer-tls)   LOGVIEWER_TLS_PORT="";   LOGVIEWER_TLS_PORT_SET=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *) echo "未知参数 $1"; usage; exit 2 ;;
    esac
done

# ★ 日志查看器 TLS 端口的默认值：**默认 8443**。
#   只在 --origin-cert（=域名走 Cloudflare）时生效——该默认值的前提是「前置 CDN 只代理固定
#   端口」，而 8443 恰在 CF 的 HTTPS 端口白名单里（8120 不在）。非 CF 部署（certbot 路线）
#   不该被自动开一个额外端口，故此时保持关闭。
#   显式传 --logviewer-tls-port <端口> 或 --no-logviewer-tls 均会覆盖此默认。
if [[ "$LOGVIEWER_TLS_PORT_SET" != 1 && "$ORIGIN_CERT" == 1 ]]; then
    LOGVIEWER_TLS_PORT="8443"
fi

[[ $EUID -ne 0 ]] && { echo "请用 sudo 执行"; exit 1; }

# 默认 INSTALL_DIR = 脚本所在目录的上级（clone 根）
if [[ -z "$INSTALL_DIR" ]]; then
    SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
    INSTALL_DIR="$(cd -- "$SCRIPT_DIR/.." &>/dev/null && pwd)"
fi

log()   { printf "\033[36m[INFO]\033[0m %s\n" "$*"; }
ok()    { printf "\033[32m[OK]\033[0m   %s\n" "$*"; }
warn()  { printf "\033[33m[WARN]\033[0m %s\n" "$*"; }
err()   { printf "\033[31m[ERROR]\033[0m %s\n" "$*"; exit 1; }

# 输入清洗：移除会破坏 sed 替换 / 正则 / nginx 配置注入的元字符（& \ /），与 deploy-linux.sh 一致。
DOMAIN="${DOMAIN//[&\\/]/}"
PUBLIC_IP="${PUBLIC_IP//[&\\/]/}"
if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    warn "DOMAIN 含非法字符（仅允许字母/数字/.-），已忽略 nginx 配置重写"
    DOMAIN=""
fi

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

# 多服务兜底探测公网 IP：任一可达即返回；用 timeout 硬包裹 curl，连 DNS 解析超时一并杀掉，
# 避免无外网/异常 DNS 时 curl 卡在解析阶段永不返回（curl --max-time 不限制 DNS 超时）。
# 返回空字符串表示全部失败。
# 审计 X-08：优先用 scripts/lib/deploy-common.sh 的公共实现（与 deploy-linux.sh 同源）。
_DEPLOY_COMMON="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/deploy-common.sh"
if [[ -f "$_DEPLOY_COMMON" ]]; then
    # shellcheck source=scripts/lib/deploy-common.sh
    source "$_DEPLOY_COMMON"
fi
if ! command -v _probe_public_ip >/dev/null 2>&1; then
    _probe_public_ip() {
        local ip=""
        for svc in https://api.ipify.org https://ifconfig.me https://icanhazip.com; do
            ip="$(timeout 8 curl -s --max-time 6 "$svc" 2>/dev/null)"
            [[ -n "$ip" ]] && { printf '%s' "$ip"; return 0; }
        done
        return 1
    }
fi
# 兜底：lib 缺失时也要有 log_viewer_port（行为与 deploy-linux.sh 一致）
if ! command -v log_viewer_port >/dev/null 2>&1; then
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
fi
if ! command -v ensure_log_viewer_public_url >/dev/null 2>&1; then
    ensure_log_viewer_public_url() {
        local env_file="$1" ip="$2" port="$3"
        [[ -n "$env_file" && -n "$ip" && -n "$port" ]] || return 1
        local host="$ip"
        [[ "$ip" == *:* && "$ip" != \[* ]] && host="[$ip]"
        local want="LOG_VIEWER_PUBLIC_URL=http://${host}:${port}/"
        if grep -q '^LOG_VIEWER_PUBLIC_URL=' "$env_file" 2>/dev/null; then
            local pattern="s|^LOG_VIEWER_PUBLIC_URL=.*|${want//|/\\|}|"
            sed -i -E "$pattern" "$env_file"
        else
            echo "$want" >> "$env_file"
        fi
        printf '%s' "$want"
    }
fi

# ---------------- 确定代码来源并拉取最新 ----------------
# 审计 X-07：改前 `git pull --ff-only` 失败只 WARN 一句就继续 —— 而"服务器访问 GitHub 抖动"
# 正是本脚本注释自述的常见场景。继续下去的后果是**代码一致性被打破**：`migrate` 会用磁盘上的
# 迁移文件改写生产库结构、`frontend-dist` 被新构建覆盖，而 `deploy/*.service` / `scripts/` /
# `backend/` 可能仍是旧版；随后脚本无条件 `systemctl restart`。典型坏组合是"新迁移 + 旧代码"。
# 现在：pull 失败**默认中止**（明确提示网络原因与镜像配置），只有显式加 `--allow-stale-code`
# 才允许降级继续；降级时记录 HEAD，并在收尾核对「跑迁移/重启前后代码是否被换过」。
PULL_FAILED=0
CODE_HEAD_BEFORE=""
CODE_HEAD_AFTER=""
_allow_stale="${ALLOW_STALE_CODE:-0}"

_pull_failed_hint() {
    warn "git pull 失败（多为服务器访问 GitHub 的网络抖动/被墙）。"
    warn "为避免「新数据库结构 + 旧代码」这类不一致状态，默认**中止更新**，服务保持原样。"
    warn "可稍后重试，或配置镜像后重跑："
    # 审计 X-26：改前这里推荐 `git config --global` —— 它会把机器上**所有**
    # https://github.com/ 请求（含携带凭据的私有仓库请求）改写到第三方代理域名。
    # 现在只推荐在当前 clone 目录里按仓库配置，并给出撤销命令。
    warn "  配置镜像（在部署目录里执行，**只影响该仓库**；不要用 --global）："
    warn "    git -C $INSTALL_DIR config url.\"https://ghproxy.net/https://github.com/\".insteadOf \"https://github.com/\""
    warn "  撤销：git -C $INSTALL_DIR config --unset url.\"https://ghproxy.net/https://github.com/\".insteadOf"
    warn "  若以前加过全局配置，请用 git config --global --unset url.\"https://ghproxy.net/https://github.com/\".insteadOf 撤销。"
    warn "镜像可用性随时间变化，也可尝试 ghfast.top / gh-proxy.com / mirror.ghproxy.com 等前缀。"
    warn "确实要在旧代码上只做数据层修复时，可显式加 --allow-stale-code 继续（会全程告警）。"
}

if [[ -d "$INSTALL_DIR/.git" ]]; then
    # 模式 A：部署目录本身是 clone → 原地 pull
    log "部署目录 $INSTALL_DIR 为 git 仓库，原地拉取最新"
    cd "$INSTALL_DIR"
    CODE_HEAD_BEFORE="$(git rev-parse HEAD 2>/dev/null || echo '')"
    if ! git pull --ff-only; then
        _pull_failed_hint
        if [[ "$_allow_stale" == 0 ]]; then
            err "已中止：git pull 失败（如确认要沿用本地旧代码，请加 --allow-stale-code）"
        fi
        PULL_FAILED=1
    fi
    CODE_HEAD_AFTER="$(git rev-parse HEAD 2>/dev/null || echo '')"
    # 自更新：若本脚本自身被本次 pull 更新，用新版本重新执行，
    #   避免用旧脚本逻辑处理新代码。环境变量哨兵防止无限 re-exec；
    #   git pull --ff-only 幂等，重跑无副作用。仅当脚本位于 INSTALL_DIR 内才 re-exec。
    if [[ "$PULL_FAILED" == 0 && -z "${GIPFEL_UPDATE_REEXEC:-}" && "$0" == "$INSTALL_DIR"/* ]]; then
        export GIPFEL_UPDATE_REEXEC=1
        log "更新脚本自身已更新，重新执行新版本"
        exec "$0" "$@"
    fi
elif [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/.git" ]]; then
    # 模式 B：从本地 source checkout pull 后 rsync 到 INSTALL_DIR（同 deploy-linux.sh 模型）
    log "从本地源码目录 $SOURCE_DIR 拉取最新并同步到 $INSTALL_DIR"
    CODE_HEAD_BEFORE="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo '')"
    if ! git -C "$SOURCE_DIR" pull --ff-only; then
        _pull_failed_hint
        if [[ "$_allow_stale" == 0 ]]; then
            err "已中止：git pull 失败（如确认要沿用本地旧代码，请加 --allow-stale-code）"
        fi
        PULL_FAILED=1
    fi
    CODE_HEAD_AFTER="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo '')"
    mkdir -p "$INSTALL_DIR"
    # 排除数据/构建产物/缓存，确保线上 db/uploads/.env 不被覆盖（同 deploy-linux.sh）
    rsync -a --delete --exclude .venv --exclude __pycache__ --exclude '*.pyc' \
        --exclude node_modules --exclude dist --exclude db.sqlite3 \
        --exclude uploads --exclude logs --exclude '.env' --exclude frontend-dist --exclude staticfiles \
        "$SOURCE_DIR/backend/"  "$INSTALL_DIR/backend/"
    rsync -a --delete --exclude node_modules --exclude dist \
        "$SOURCE_DIR/frontend/" "$INSTALL_DIR/frontend/"
    rsync -a --delete "$SOURCE_DIR/deploy/"   "$INSTALL_DIR/deploy/"
    # scripts/ 也同步：否则服务器上永远跑旧版更新脚本（masked 自愈等修复无法生效）
    rsync -a --delete "$SOURCE_DIR/scripts/"  "$INSTALL_DIR/scripts/"
elif [[ -n "$REPO" ]]; then
    # 模式 C：克隆到 INSTALL_DIR（要求目录为空）
    if [[ -e "$INSTALL_DIR" && -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]]; then
        err "INSTALL_DIR ($INSTALL_DIR) 非空，无法 git clone。请清空后重试，或改用 --source-dir。"
    fi
    log "克隆仓库 $REPO → $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    git clone "$REPO" "$INSTALL_DIR"
else
    err "无法确定代码来源：INSTALL_DIR ($INSTALL_DIR) 不是 git 仓库；也未提供 --source-dir 或 --repo。请先 git clone，或用 deploy-linux.sh 首次部署。"
fi


# ---------------- 运行用户（幂等）----------------
if ! id gipfel >/dev/null 2>&1; then
    log "创建专用运行用户 gipfel"
    useradd -r -s /usr/sbin/nologin -U -d "$INSTALL_DIR" gipfel || true
fi

# ---------------- 拉取已完成（见上方代码获取逻辑）----------------
ok "代码已更新到最新，开始应用更新"

# ---------------- 1. 备份数据库/上传/配置 ----------------
# 审计 X-10：migrate 之后还有 collectstatic / 前端构建 / 重启服务等步骤，任何一步失败都会
# 留下"新库结构 + 旧代码 + 服务停摆"；这里注册失败陷阱，把回滚命令说清楚。
MIGRATE_STARTED=0
MIGRATE_OK=0
PRE_MIGRATE_DB_SNAPSHOT=""
_on_exit() {
    local rc=$?
    if [[ "$rc" != "0" && "$MIGRATE_STARTED" == "1" && "$MIGRATE_OK" != "1" ]]; then
        echo
        warn "脚本以退出码 $rc 结束，且已执行过 migrate（或正在执行）—— 数据库结构可能已改变。"
        print_rollback_hint "$INSTALL_DIR" "$PRE_MIGRATE_DB_SNAPSHOT" "$BACKUP_DIR" "$CODE_HEAD_BEFORE"
    fi
    exit "$rc"
}
trap _on_exit EXIT

BACKUP_DIR="$INSTALL_DIR/_backup/$(date +%F_%H%M%S)"
mkdir -p "$BACKUP_DIR"
if [[ -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
    # 审计 X-10：改前是 `cp -a` 活库 —— WAL 下可能得到不一致的副本，而它是唯一回滚副本。
    if snapshot_sqlite_consistent "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/db.sqlite3"; then
        PRE_MIGRATE_DB_SNAPSHOT="$BACKUP_DIR/db.sqlite3"
        log "已做数据库一致性快照 → $PRE_MIGRATE_DB_SNAPSHOT"
    else
        err "无法为现有数据库生成一致性快照（$BACKUP_DIR/db.sqlite3）—— 没有它就无法回滚，已中止。"
    fi
fi
[[ -d "$INSTALL_DIR/backend/uploads" ]] && cp -a "$INSTALL_DIR/backend/uploads" "$BACKUP_DIR/" 2>/dev/null || true
[[ -f "$INSTALL_DIR/backend/.env" ]]    && cp -a "$INSTALL_DIR/backend/.env"    "$BACKUP_DIR/" 2>/dev/null || true

# ---------------- 2. 更新后端依赖 + 迁移 ----------------
# 首跑引导：.env 不存在（全新 clone 未跑过 deploy-linux.sh）→ 从 example 生成，
# 否则后端 settings.py 因缺 JWT_SECRET fail-fast，下方 manage.py 直接中止
if [[ ! -f "$INSTALL_DIR/backend/.env" ]]; then
    log "未检测到 backend/.env，按首次部署生成（随机 JWT_SECRET / LOGVIEWER_SECRET_KEY / SEED_ADMIN_PASSWORD，DEBUG=false）"
    cp "$INSTALL_DIR/backend/.env.example" "$INSTALL_DIR/backend/.env"
    SECRET="$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=')"
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${SECRET}|" "$INSTALL_DIR/backend/.env"
    LVSECRET="$(head -c 32 /dev/urandom | base64 | tr -d '\n+/=')"
    if grep -q '^LOGVIEWER_SECRET_KEY=' "$INSTALL_DIR/backend/.env"; then
        sed -i -E "s|^LOGVIEWER_SECRET_KEY=.*|LOGVIEWER_SECRET_KEY=${LVSECRET}|" "$INSTALL_DIR/backend/.env"
    else
        echo "LOGVIEWER_SECRET_KEY=${LVSECRET}" >> "$INSTALL_DIR/backend/.env"
    fi
    # 默认管理员密码：首次部署自动生成强随机密码
    ADMIN_PW="$(head -c 16 /dev/urandom | base64 | tr -d '\n+/=' | head -c 20)"
    if grep -q '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env"; then
        sed -i -E "s|^SEED_ADMIN_PASSWORD=.*|SEED_ADMIN_PASSWORD=${ADMIN_PW}|" "$INSTALL_DIR/backend/.env"
    else
        echo "SEED_ADMIN_PASSWORD=${ADMIN_PW}" >> "$INSTALL_DIR/backend/.env"
    fi
    if grep -q '^DEBUG=' "$INSTALL_DIR/backend/.env"; then
        sed -i 's/^DEBUG=.*/DEBUG=false/' "$INSTALL_DIR/backend/.env"
    else
        echo 'DEBUG=false' >> "$INSTALL_DIR/backend/.env"
    fi
    warn "已生成 .env；公网访问入口（DJANGO_ALLOWED_HOSTS/CORS/CSRF）将在下方自愈块按域名/公网 IP 补全"
    warn "默认管理员密码已自动生成，请查看 .env 中的 SEED_ADMIN_PASSWORD（首次登录后强制修改）"
fi
log "更新后端（pip / migrate / collectstatic）"
# 审计 X-07：降级（--allow-stale-code 且 pull 失败）时，写库结构变更属于高风险操作 ——
# 明确告警，并在收尾核对「迁移/构建/重启前后代码 HEAD 是否被换过」。
if [[ "${PULL_FAILED:-0}" == 1 ]]; then
    warn "⚠ 本次沿用本地旧代码（git pull 失败）：即将执行 migrate / npm run build / restart。"
    warn "  若这些旧代码与远端（或上次半途更新留下的）迁移文件不一致，可能产生「新库结构 + 旧代码」。"
fi
cd "$INSTALL_DIR/backend"
if [[ ! -d .venv ]]; then
    log "虚拟环境不存在，创建并安装依赖"
    python3 -m venv .venv
    ".venv/bin/pip" install --upgrade pip setuptools wheel
fi
".venv/bin/pip" install -r requirements.txt
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
ok "后端依赖与数据库迁移完成"

# ---------------- 3. 更新前端构建 ----------------
log "更新前端（npm ci + build）"
cd "$INSTALL_DIR/frontend"
if [[ ! -d node_modules ]]; then
    npm ci --no-audit --no-fund
else
    # package-lock 变更时增量装；失败回退到 npm ci 保证一致性
    npm install --no-audit --no-fund 2>/dev/null || npm ci --no-audit --no-fund
fi
npm run build
rm -rf "$INSTALL_DIR/frontend-dist"
mkdir -p "$INSTALL_DIR/frontend-dist"
cp -a dist/. "$INSTALL_DIR/frontend-dist/"

# ---------------- 4. 文件归属与权限 ----------------
# 所有步骤以 root 身份写入（.venv / db.sqlite3 / uploads / logs / frontend-dist），
# 统一归属运行用户 gipfel，否则 systemd 以 gipfel 启动时无写权限。
chown -R gipfel:gipfel "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/backend/.env" 2>/dev/null || true
ok "文件归属已切换为 gipfel，.env 权限收紧为 600"

# 自愈：无域名部署时，纠正/补全 .env 中 LOG_VIEWER_PUBLIC_URL。
#   - 显式 --public-ip：无论当前有无/对错，都以它为准写入（覆盖内网 IP 或缺失该行）
#   - 否则：仅当当前值缺失或指向内网/私网 IP 才纠正；已是公网 IP/域名则不动
# 公网 IP 探测失败则移除该行，改由后端按请求 Host 推导（nginx 透传 $host=公网 IP）。
LV_PUBLIC_IP=""   # 预初始化：set -u 下后续 ALLOWED_HOSTS 自愈块可能在其未赋值时引用
if [[ -z "$DOMAIN" && -f "$INSTALL_DIR/backend/.env" ]]; then
    # ★ 整条管道必须容忍「无匹配」：.env 里没有 LOG_VIEWER_PUBLIC_URL 时 grep 退出 1，
    #   在 set -o pipefail 下整条管道即为失败；而这是**变量赋值**，赋值的退出码就是命令替换的
    #   退出码 → set -e 直接终止脚本，且不打印任何东西。
    #   真实事故：域名部署（脚本不写 LOG_VIEWER_PUBLIC_URL）+ 升级时未传 --domain
    #   → 走进本分支 → 本行终止 → 现象是「脚本跑到文件归属那步就没了」。
    LV_CUR="$(grep -E '^LOG_VIEWER_PUBLIC_URL=' "$INSTALL_DIR/backend/.env" | tail -n1 | sed -E 's#^LOG_VIEWER_PUBLIC_URL=https?://##; s#[/:].*##' || true)"
    LV_NEED_FIX=0
    if [[ -n "$PUBLIC_IP" ]]; then
        LV_NEED_FIX=1   # 显式指定：强制以 --public-ip 为准（覆盖内网 IP 或缺失行）
    elif [[ -z "$LV_CUR" ]]; then
        LV_NEED_FIX=1   # 缺失：需要补全（此前漏过缺失场景，后端回退 127.0.0.1）
    elif [[ "$LV_CUR" =~ ^10\. ]] || \
         [[ "$LV_CUR" =~ ^192\.168\. ]] || \
         [[ "$LV_CUR" =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]] || \
         [[ "$LV_CUR" =~ ^169\.254\. ]] || \
         [[ "$LV_CUR" =~ ^127\. ]]; then
        LV_NEED_FIX=1
    fi
    if [[ $LV_NEED_FIX -eq 1 ]]; then
        # 审计 X-08：改前这三处把端口**硬编码成 8120**，而 nginx 用 .env 的 LOG_VIEWER_PORT
        # （本脚本第 485 行自己也读了 `_lv_port`）—— 把端口改成非 8120 后，升级会把 URL 与
        # 防火墙规则改回 8120，前端按钮跳错端口、运维查防火墙也被误导。现在统一取同一端口。
        _lv_port_fix="$(log_viewer_port "$INSTALL_DIR/backend/.env")"
        if [[ -n "$PUBLIC_IP" ]]; then
            LV_PUBLIC_IP="$(normalize_ip "$PUBLIC_IP")"
            _written="$(ensure_log_viewer_public_url "$INSTALL_DIR/backend/.env" "$LV_PUBLIC_IP" "$_lv_port_fix")"
            warn "已按 --public-ip 写入 ${_written}（端口 ${_lv_port_fix}；原值：${LV_CUR:-无}）。"
        else
            # 多服务兜底探测公网 IP（任一可达即可）；均失败则回退「移除该行，交给后端按 Host 推导」
            # 注意必须 || true：set -e 下 _probe_public_ip 全部失败返回 1 会直接终止脚本
            LV_PUBLIC_IP="$(_probe_public_ip)" || true
            if [[ -n "$LV_PUBLIC_IP" && "$LV_PUBLIC_IP" != "$LV_CUR" ]]; then
                LV_PUBLIC_IP="$(normalize_ip "$LV_PUBLIC_IP")"
                _written="$(ensure_log_viewer_public_url "$INSTALL_DIR/backend/.env" "$LV_PUBLIC_IP" "$_lv_port_fix")"
                warn "已写入 ${_written}（端口 ${_lv_port_fix}；原值：${LV_CUR:-缺失}）。"
            else
                if [[ -n "$LV_CUR" ]]; then
                    sed -i -E "/^LOG_VIEWER_PUBLIC_URL=/d" "$INSTALL_DIR/backend/.env"
                    warn "公网 IP 探测失败，已移除 .env 中指向内网 IP(${LV_CUR}) 的 LOG_VIEWER_PUBLIC_URL，改由后端按请求 Host 推导公网地址。"
                fi
                # 无旧值且探测失败：什么都不做，后端会按 Host 推导
            fi
        fi
    fi
fi

# 自愈：DJANGO_ALLOWED_HOSTS 必须包含公网访问入口（域名或公网 IP）。
#   缺失时 Django 对一切非回环 Host 的请求返回原生 400 Bad Request（登录/健康检查
#   全部失败，前端表现为「请求参数错误」）。纯 IP 部署（无 --domain）在更新脚本中
#   之前完全没有此自愈，升级后仍会 400。幂等：白名单里已有该条目则不重复追加。
#   优先顺序：--domain > --public-ip > 自动探测公网 IP。
#   ★ 域名模式下必须**同时**加入 log.<DOMAIN>：日志查看器是独立 Django 服务，它自己的
#     ALLOWED_HOSTS 靠本项兜底纳入。缺它则 nginx 以 Host: log.<域名> 反代过去会被
#     日志查看器 400 拒绝，现象是「主站正常、日志查看器打不开」。
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
        if grep -q '^DJANGO_ALLOWED_HOSTS=' "$INSTALL_DIR/backend/.env"; then
            for _ah in "${AH_ENTRIES[@]}"; do
                if ! grep -E "^DJANGO_ALLOWED_HOSTS=" "$INSTALL_DIR/backend/.env" | grep -qE "(^|,)${_ah}(,|$)"; then
                    sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|&,${_ah}|" "$INSTALL_DIR/backend/.env"
                    ok "DJANGO_ALLOWED_HOSTS 已追加公网入口：${_ah}"
                fi
            done
        else
            _ah_new="$(IFS=,; echo "${AH_ENTRIES[*]}")"
            echo "DJANGO_ALLOWED_HOSTS=${_ah_new},localhost,127.0.0.1" >> "$INSTALL_DIR/backend/.env"
            ok "DJANGO_ALLOWED_HOSTS 已写入：${_ah_new},localhost,127.0.0.1"
        fi
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
    #     · Host 若为 IP → 推导出 http://<IP>:8120/，在 CF 代理下必然打不开
    #   显式写死之后只有一个权威值，且脚本收尾能自检（见文件末尾「部署自检」）。
    #
    # 换端口的原因：CF 只代理固定端口（HTTP 80/8080/8880/2052/2082/2086/2095，
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
            warn "log.${DOMAIN} 解析不到（无 DNS 记录）——日志查看器按钮将打不开。"
            warn "  若无法添加该三级记录，请改用 --origin-cert（TLS 端口默认 8443，不需要该子域）。"
        fi
    fi
    # sed -i 会以 root 重建 .env（属主变为 root），恢复运行用户归属与 600 权限，
    # 否则 systemd 以 gipfel 启动时读不到 .env（LOG_VIEWER_PUBLIC_URL 自愈块同理）。
    chown gipfel:gipfel "$INSTALL_DIR/backend/.env" 2>/dev/null || true
    chmod 600 "$INSTALL_DIR/backend/.env" 2>/dev/null || true
fi

# ---------------- 5. 刷新并重启 systemd 服务 ----------------
# 重要：升级时必须把最新的 deploy/*.service 重新落到 /etc/systemd/system/（替换 __INSTALL_DIR__），
#        否则仓库中对服务单元的改动（如日志查看器 8121 端口修复、ExecStart 变更）不会传播到 live 单元，
#        重启后仍使用旧配置。与 deploy-linux.sh 第 6 步保持一致。
refresh_unit() {
    local src="$1" name="$2" attempt
    for attempt in 1 2; do
        [[ -f "$src" ]] || { warn "找不到服务单元模板 $src，跳过刷新 $name"; return; }
        # 模板为空（历史 bug：`>` 先清空与模板同路径的渲染产物）→ 自动恢复后重试：
        # 优先 --source-dir 的模板副本（模式 B 部署目录不是 git 仓库），否则 git checkout 恢复
        if [[ ! -s "$src" ]]; then
            if [[ -n "$SOURCE_DIR" && -s "$SOURCE_DIR/deploy/$(basename "$src")" ]]; then
                cp -f "$SOURCE_DIR/deploy/$(basename "$src")" "$src"
                warn "模板 $src 为空，已从源码目录恢复"
            elif git -C "$INSTALL_DIR" checkout -- "deploy/$(basename "$src")" 2>/dev/null && [[ -s "$src" ]]; then
                warn "模板 $src 为空，已从 git 仓库恢复"
            elif [[ $attempt == 1 ]]; then
                warn "模板 $src 为空且暂无法恢复，重试前再试一次"
            else
                warn "模板 $src 为空且无法从 git/源码目录恢复，跳过安装 $name"
                return 1
            fi
        fi
        # masked 自愈：mask 有两种落点，且 cp -f 会跟随 /dev/null 软链把内容写进 /dev/null，
        # 故必须先解除再写入——
        #   永久：/etc/systemd/system/$name → /dev/null
        #   运行时：/run/systemd/system/$name → /dev/null（/run 优先级高于 /etc，
        #           即使 /etc 写入真实 unit，运行时 mask 仍会遮蔽 → enable/restart 报 masked）
        # systemctl unmask 本身会同时清理 /etc 与 /run 两处，必须无条件执行：
        # 仅凭「/etc 下存在 /dev/null 软链」检测会漏掉运行时 mask（曾真实发生）。
        systemctl unmask "$name" 2>/dev/null || true
        rm -f "/etc/systemd/system/$name" "/run/systemd/system/$name"
        # 渲染到独立临时文件：目标与模板同路径时，`>` 会先清空文件、sed 再读到空内容，
        # 模板与产物一起变 0 字节——systemd 把空 unit 文件按 masked 处理（真实事故）。
        local tmp; tmp="$(mktemp "/tmp/${name}.XXXXXX")"
        sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$src" > "$tmp"
        # 渲染产物必须非空：空文件装上去即是 masked，宁可不装也不要破坏现场
        if [[ ! -s "$tmp" ]]; then
            rm -f "$tmp"
            warn "渲染 $name 得到空文件（模板 $src 可能为空或被截断）"
            [[ $attempt == 1 ]] && continue
            return 1
        fi
        cp -f "$tmp" "/etc/systemd/system/$name"
        rm -f "$tmp"
        # 让 systemd 重新扫描 unit（清掉内存中残留的 masked 状态），再 enable
        systemctl daemon-reload
        if systemctl enable "$name" 2>/dev/null; then
            ok "已刷新并启用服务单元 $name → /etc/systemd/system/$name"
            return 0
        fi
        if [[ $attempt == 1 ]]; then
            warn "systemctl enable $name 第 1 次失败，自动恢复后重试"
            continue
        fi
        # 最终失败：输出诊断——LoadState / FragmentPath 直接揭示 masked 的真实来源
        # （空文件与 /dev/null 软链都会被判为 masked；mask 也可能存在于搜索路径的任何一层）
        warn "systemctl enable $name 两次尝试均失败，自动诊断如下："
        systemctl show -p LoadState,FragmentPath "$name" 2>/dev/null | while IFS= read -r line; do
            warn "  $line"
        done
        while IFS= read -r f; do
            warn "  $(ls -la "$f" 2>/dev/null | tail -n1)"
        done < <(find /etc/systemd/system /run/systemd/system /usr/local/lib/systemd/system \
                      /usr/lib/systemd/system /lib/systemd/system \
                      -maxdepth 1 -name "$name" 2>/dev/null)
        return 1
    done
}
UNIT_REFRESH_FAILED=0
refresh_unit "$INSTALL_DIR/deploy/gipfel.service" gipfel.service || UNIT_REFRESH_FAILED=1
refresh_unit "$INSTALL_DIR/deploy/logviewer.service" gipfel-logviewer.service || UNIT_REFRESH_FAILED=1
systemctl daemon-reload

if [[ "$UNIT_REFRESH_FAILED" == 1 ]]; then
    warn "服务单元未能正常启用（masked 等问题未解除），本次跳过全部重启，避免用旧状态误判。"
    warn "请按上方诊断处理模板/软链后执行：sudo systemctl daemon-reload && sudo systemctl restart gipfel gipfel-logviewer"
elif systemctl cat gipfel.service >/dev/null 2>&1; then
    log "重启 gipfel.service"
    systemctl restart gipfel
    sleep 2
    if ! systemctl is-active --quiet gipfel; then
        sleep 5
    fi
    systemctl is-active --quiet gipfel && ok "gipfel.service 已重启并运行中" || \
        { journalctl -u gipfel -n 30 --no-pager; err "gipfel 启动失败，见上方日志（可用备份 $BACKUP_DIR 回滚）"; }
else
    warn "gipfel.service 尚未注册（首次部署请先运行 deploy-linux.sh），跳过重启"
fi

# 日志查看器（独立站点）；刷新失败时同样跳过重启
if [[ "$UNIT_REFRESH_FAILED" == 1 ]]; then
    warn "单元刷新失败，跳过 gipfel-logviewer 重启"
elif systemctl cat gipfel-logviewer.service >/dev/null 2>&1; then
    log "重启 gipfel-logviewer.service"
    systemctl restart gipfel-logviewer
    sleep 2
    if ! systemctl is-active --quiet gipfel-logviewer; then
        sleep 5
    fi
    systemctl is-active --quiet gipfel-logviewer && ok "gipfel-logviewer.service 已重启并运行中" || \
        { journalctl -u gipfel-logviewer -n 30 --no-pager; err "gipfel-logviewer 启动失败，见上方日志（可用备份 $BACKUP_DIR 回滚）"; }
else
    warn "gipfel-logviewer.service 尚未注册（首次部署请先运行 deploy-linux.sh），跳过重启"
fi

# ---------------- 6. nginx（可选）----------------
if [[ $WITH_NGINX -eq 1 ]]; then
    VHOST_TMPL="$INSTALL_DIR/deploy/nginx-gipfel.conf"
    [[ -f "$VHOST_TMPL" ]] || err "找不到 nginx 模板：$VHOST_TMPL"
    # 模板为空（历史 bug：deploy-linux.sh 原地部署时渲染目标与模板同路径被 `>` 清空）→ 从 git 恢复
    if [[ ! -s "$VHOST_TMPL" ]]; then
        if git -C "$INSTALL_DIR" checkout -- deploy/nginx-gipfel.conf 2>/dev/null && [[ -s "$VHOST_TMPL" ]]; then
            warn "nginx 模板为空，已从 git 仓库恢复"
        else
            err "nginx 模板为空且无法从 git 恢复：$VHOST_TMPL"
        fi
    fi
    VHOST_OUT="/etc/nginx/sites-available/gipfel.conf"
    log "重新生成 nginx 虚拟主机"
    # ★ 防「静默摧毁 HTTPS」+ 留可回滚副本。
    #   下面把模板渲染结果用 `> "$VHOST_OUT"` 写入时是**整体覆盖**；而 certbot 的 nginx
    #   插件是把 443 块**直接写进同一个文件**的。若现有 vhost 已含生效的 443，而本次没有
    #   --origin-cert 去重新生成它，覆盖后 443 就消失、HTTPS 静默失效（全程无任何报错）。
    #   宁可中止，也不静默摧毁——这正是「昨天还好好的，今天 HTTPS 就没了」的成因。
    if [[ -f "$VHOST_OUT" ]]; then
        cp -f "$VHOST_OUT" "${VHOST_OUT}.bak-$(date +%F_%H%M%S)" 2>/dev/null || true
        if [[ "$ORIGIN_CERT" != 1 ]] && grep -qE '^[[:space:]]*listen[[:space:]]+443' "$VHOST_OUT"; then
            err "现有 vhost 已配置 443，而本次未传 --origin-cert——覆盖会让 HTTPS 配置消失（且不报错）。
    请二选一：
      A) 改由脚本管理 HTTPS：把证书放到 ${ORIGIN_CERT_DIR}/<域名>.pem|.key，加 --origin-cert 重跑；
      B) 继续由 certbot 管理：本次**不要加 --with-nginx**（只升级代码与服务），
         或先把 443 块移到 /etc/nginx/snippets/ 下再用 include 引入。
    旧 vhost 已备份为 ${VHOST_OUT}.bak-<时间戳>，可随时还原。"
        fi
        # 同类防护（日志查看器非标准 TLS 端口）：若现有 vhost 已在 8443 之类的端口上提供
        # TLS，而本次未传 --logviewer-tls-port，覆盖后该块会消失 —— 前端按钮随即指向空气。
        # 注意必须带 || true：管道里首个 grep 无匹配会退出 1，pipefail 下会让赋值失败并终止脚本。
        _stale_tls_ports="$(grep -oE '^[[:space:]]*listen[[:space:]]+[0-9]+[[:space:]]+ssl' "$VHOST_OUT" 2>/dev/null \
                            | grep -oE '[0-9]+' | grep -v '^443$' | sort -u | tr '\n' ' ' || true)"
        if [[ -z "$LOGVIEWER_TLS_PORT" && -n "$_stale_tls_ports" ]]; then
            err "现有 vhost 已在非标准端口上提供 TLS（端口：${_stale_tls_ports}），而本次未传 --logviewer-tls-port——
    覆盖后这些端口块会消失，日志查看器可能因此打不开（且不报错）。
    若确实在用，请带上 --logviewer-tls-port <端口> 重跑；
    若有意移除，请手动确认后再执行（旧 vhost 已备份）。"
        fi
    fi
    # 解析 .env 的 LOG_VIEWER_PORT（默认 8120；缺失/非法/越界一律兜底），与 deploy-linux.sh 的
    # _log_viewer_port 同语义。nginx 模板里 listen 端口是 __LOG_VIEWER_PORT__ 占位符，daphne
    # 内部 8121 不受其影响（避免同机抢端口）。
    _lv_port="8120"
    if [[ -f "$INSTALL_DIR/backend/.env" ]] && grep -qE '^[[:space:]]*LOG_VIEWER_PORT=' "$INSTALL_DIR/backend/.env"; then
        _lv_port=$(grep -E '^[[:space:]]*LOG_VIEWER_PORT=' "$INSTALL_DIR/backend/.env" | head -1 | cut -d= -f2- \
            | tr -d '[:space:]' | sed -E "s/^['\"]//; s/['\"]$//")
        if ! [[ "$_lv_port" =~ ^[0-9]+$ ]] || (( _lv_port < 1 || _lv_port > 65535 )); then
            warn "LOG_VIEWER_PORT=$_lv_port 非法（需 1-65535 整数），回退默认 8120"
            _lv_port="8120"
        fi
    fi
    log "日志查看器公网监听端口：${_lv_port}（daphne 内部仍绑 8121）"
    sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
        -e "s|__DOMAIN__|${DOMAIN:-_}|g" \
        -e "s|__LOG_VIEWER_PORT__|${_lv_port}|g" \
        "$VHOST_TMPL" > "$VHOST_OUT"
    # 防御：模板与脚本版本撕裂（服务器上 deploy/nginx-gipfel.conf 是新版带占位符，
    # 但本脚本是旧版没替换逻辑）会留下字面量 __LOG_VIEWER_PORT__ 进了 nginx，nginx -t
    # 会报 "host not found in __LOG_VIEWER_PORT__" → 整个部署中断。检测到残留就
    # 用旧版兼容（占位符 → ${_lv_port}）兜底，并强烈告警让运维对齐脚本版本。
    if grep -q '__LOG_VIEWER_PORT__' "$VHOST_OUT"; then
        warn "vhost 残留 __LOG_VIEWER_PORT__（脚本与模板版本撕裂），fallback 用本脚本内联值 ${_lv_port} 兜底"
        sed -i "s|__LOG_VIEWER_PORT__|${_lv_port}|g" "$VHOST_OUT"
        warn "请将 scripts/update-from-github.sh 与 deploy/nginx-gipfel.conf 同步升级到同一 commit 后再跑（避免再触发）"
    fi
    # 按是否传 --domain 保留日志查看器对应的 server 块（与 deploy-linux.sh 一致）：
    #   有域名 → 保留 log.<DOMAIN> 子域块，删除 8120 端口块；
    #   无域名（纯 IP）→ 保留 8120 端口块，删除子域块（server_name log._ 形同失效，移除避免歧义）。
    if [[ -n "$DOMAIN" ]]; then
        sed -i '/# === LOGVIEWER_PORT8120_START ===/,/# === LOGVIEWER_PORT8120_END ===/d' "$VHOST_OUT"
    else
        sed -i '/# === LOGVIEWER_SUBDOMAIN_START ===/,/# === LOGVIEWER_SUBDOMAIN_END ===/d' "$VHOST_OUT"
    fi

    # ---------------- HTTPS：启用 443（Cloudflare Origin Certificate）----------------
    # 与 deploy-linux.sh 同一套语义与同一份模板标记。443 块在模板里默认是**注释态**；
    # 本段按 --origin-cert 取消注释并填入证书路径。
    # ★ 升级场景的意义：证书已经装过一次，这里只负责「每次重渲染 vhost 后把 443 重新启用」，
    #   否则重跑升级脚本会用**未启用 HTTPS 的模板产物**覆盖现有 vhost —— 443 静默消失。
    # ⚠️ 走 certbot 的用户不要加 --origin-cert（certbot 自己写 443 块，两者会重复 server_name）。
    if [[ "$ORIGIN_CERT" == 1 ]]; then
        if [[ "$WITH_NGINX" != 1 ]]; then
            err "--origin-cert 需要与 --with-nginx 一起使用（否则没有可改写的 vhost）"
        fi
        if [[ -z "$DOMAIN" ]]; then
            err "--origin-cert 需要同时传 --domain（默认用它推导证书文件名 <目录>/<域名>.pem|.key；
     如需自定义路径请用 --ssl-cert / --ssl-key）"
        fi
        [[ -n "$SSL_CERT" ]] || SSL_CERT="${ORIGIN_CERT_DIR}/${DOMAIN}.pem"
        [[ -n "$SSL_KEY"  ]] || SSL_KEY="${ORIGIN_CERT_DIR}/${DOMAIN}.key"
        for _f in "$SSL_CERT" "$SSL_KEY"; do
            if [[ ! -s "$_f" ]]; then
                err "证书文件不存在或为空：$_f
  请先在 Cloudflare 面板「SSL/TLS → 源服务器 → 创建证书」签发（Hostnames 填
  ${DOMAIN} 与 log.${DOMAIN}，或 *.${DOMAIN}），再放好两个文件：
    sudo install -d -m 755 ${ORIGIN_CERT_DIR}
    sudo install -m 644 <下载的 cert.pem> ${ORIGIN_CERT_DIR}/${DOMAIN}.pem
    sudo install -m 600 <下载的 key.pem>  ${ORIGIN_CERT_DIR}/${DOMAIN}.key"
            fi
        done
        sed -i -E "/^# === NGINX_SSL_443_START ===$/,/^# === NGINX_SSL_443_END ===$/ { /^# === NGINX_SSL/! { s/^# //; s/^#$// } }" "$VHOST_OUT"
        sed -i -E "/^# === NGINX_SSL_443_LOGVIEWER_START ===$/,/^# === NGINX_SSL_443_LOGVIEWER_END ===$/ { /^# === NGINX_SSL/! { s/^# //; s/^#$// } }" "$VHOST_OUT"
        # 日志查看器「域名 + 非标准 TLS 端口」块（--logviewer-tls-port）：
        #   域名走 Cloudflare 时 CF 只代理固定端口，默认 8120 不在其中，而 log.<域名>
        #   又可能加不了记录 —— 于是用同一主机名的 CF 受支持端口（8443）。
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            sed -i -E "/^# === LOGVIEWER_TLS_PORT_START ===$/,/^# === LOGVIEWER_TLS_PORT_END ===$/ { /^# === LOGVIEWER_TLS/! { s/^# //; s/^#$// } }" "$VHOST_OUT"
        else
            # 未启用：整段删除，避免残留 __LOG_VIEWER_TLS_PORT__ 占位符让 nginx -t 失败
            sed -i '/^# === LOGVIEWER_TLS_PORT_START ===$/,/^# === LOGVIEWER_TLS_PORT_END ===$/d' "$VHOST_OUT"
        fi
        # ★ 占位符替换必须**先于下面所有检查**：检查的是「替换完成后的最终产物」。
        #   曾把 `grep 'listen <端口> ssl'` 放在端口替换之前 → 那一刻产物里还是
        #   `listen __LOG_VIEWER_TLS_PORT__ ssl;`，grep 必然失败并误报「模板被改动」。
        sed -i -e "s|__SSL_CERT__|${SSL_CERT}|g" -e "s|__SSL_KEY__|${SSL_KEY}|g" "$VHOST_OUT"
        if [[ -n "$LOGVIEWER_TLS_PORT" ]]; then
            sed -i "s|__LOG_VIEWER_TLS_PORT__|${LOGVIEWER_TLS_PORT}|g" "$VHOST_OUT"
        fi
        if grep -q '__SSL_CERT__\|__SSL_KEY__\|__LOG_VIEWER_TLS_PORT__' "$VHOST_OUT"; then
            err "vhost 仍残留占位符（__SSL_CERT__/__SSL_KEY__/__LOG_VIEWER_TLS_PORT__），模板与脚本版本不一致；请把 deploy/nginx-gipfel.conf 与 scripts/ 同步到同一 commit 后重跑"
        fi
        if [[ -n "$LOGVIEWER_TLS_PORT" ]] \
           && ! grep -qE "^[[:space:]]*listen[[:space:]]+${LOGVIEWER_TLS_PORT}[[:space:]]+ssl;" "$VHOST_OUT"; then
            err "已请求 --logviewer-tls-port ${LOGVIEWER_TLS_PORT}，但产物里没有 'listen ${LOGVIEWER_TLS_PORT} ssl;' —— 模板的 LOGVIEWER_TLS_PORT 标记可能被改动，请检查 deploy/nginx-gipfel.conf"
        fi
        if ! grep -q 'listen 443 ssl' "$VHOST_OUT"; then
            err "已在 --origin-cert 模式下渲染，但产物里没有 listen 443 ssl —— 模板的 SSL 标记可能被改动，请检查 deploy/nginx-gipfel.conf"
        fi
        # 自检：区域内若混入「散文注释」，取消一层注释后会变成非法指令。
        # 曾真实发生：unknown directive "日志查看器子域的" —— nginx 的报错不会说明是
        # 模板问题，运维很难定位，故提前用可读错误拦住。
        # 注意必须先 `sed 's/#.*$//'` 剥掉**行内注释**再判断：模板里存在合法行内注释
        # （如 `proxy_read_timeout 86400s;   # 长连接`），只看行首会把它误判成散文。
        _bad_prose="$(LC_ALL=C sed 's/#.*$//' "$VHOST_OUT" 2>/dev/null \
                      | LC_ALL=C grep -n '[^ -~]' | head -3 || true)"
        if [[ -n "$_bad_prose" ]]; then
            err "渲染后的 vhost 出现「生效的非 ASCII 行」——SSL 标记区域内混入了散文注释（或行内注释前的指令含非 ASCII）：
${_bad_prose}
  NGINX_SSL_443* 区域内只允许放【注释形式的 nginx 配置】；说明文字必须写在标记行之外。
  区域内确需写注释时用两层井号（\`#     # 说明\`），取消一层后仍是注释。"
        fi
        ok "已启用 HTTPS：证书 ${SSL_CERT}；服务 ${DOMAIN} 与 log.${DOMAIN}（443）"
    fi
    # 按 nginx.conf 实际 include 风格放置 gipfel 配置（兼容 sites-enabled 与仅 include conf.d 的精简镜像）
    if grep -q 'sites-enabled' /etc/nginx/nginx.conf 2>/dev/null; then
        ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/gipfel.conf
        rm -f /etc/nginx/conf.d/gipfel.conf
        ok "已启用 gipfel nginx 虚拟主机（sites-enabled/gipfel.conf）"
    elif grep -q 'conf.d' /etc/nginx/nginx.conf 2>/dev/null; then
        cp -f /etc/nginx/sites-available/gipfel.conf /etc/nginx/conf.d/gipfel.conf
        rm -f /etc/nginx/sites-enabled/gipfel.conf
        ok "已启用 gipfel nginx 虚拟主机（conf.d/gipfel.conf，因 nginx.conf 仅 include conf.d）"
    else
        ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/gipfel.conf
        cp -f /etc/nginx/sites-available/gipfel.conf /etc/nginx/conf.d/gipfel.conf
        ok "已启用 gipfel nginx 虚拟主机（sites-enabled + conf.d 均放置，兜底）"
    fi
    # 禁用 nginx 自带默认欢迎页：枚举所有已知变体并删除，否则 80 端口可能被默认站点抢走
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
    # 全新服务器 nginx 可能尚未启动，reload 对未运行服务会失败；按状态选择 start / reload
    systemctl enable nginx 2>/dev/null || true
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
        ok "nginx 配置已 reload"
    else
        systemctl start nginx
        ok "nginx 已启动"
    fi
    # 验证：80 端口不应再返回 nginx 默认欢迎页；若后端未起则给出 502 排查提示而非误判
    sleep 1
    if command -v curl >/dev/null 2>&1; then
        _body="$(curl -s --max-time 5 http://127.0.0.1/ 2>/dev/null || true)"
        if printf '%s' "$_body" | grep -qi 'Welcome to nginx'; then
            warn "80 端口仍返回 nginx 默认欢迎页：默认站点未被完全禁用。请检查 /etc/nginx/nginx.conf 是否内联了默认 server 块，或仍有其它 sites-enabled/* 配置冲突"
        elif ! systemctl is-active --quiet gipfel; then
            warn "nginx 已正确接管 80 端口，但后端 gipfel 服务未运行，访问将出现 502；请执行：sudo systemctl restart gipfel"
        else
            ok "80 端口验证通过：gipfel 站点已生效（非默认欢迎页）"
        fi
    fi
    # 80/443 放行（与 deploy-linux.sh 一致）：HTTPS 启用后 443 必需；
    #   经 CDN（Cloudflare 等）回源时同样要能连上 443，否则 CDN 报 521。
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
    # 无域名：日志查看器经 8120 端口暴露公网，需放行防火墙（与 deploy-linux.sh 一致）
    if [[ -z "$DOMAIN" ]]; then
        _lv_port_ufw="$(log_viewer_port "$INSTALL_DIR/backend/.env")"
        if command -v ufw >/dev/null 2>&1; then
            # 清理历史遗留的 8120 规则，再放行实际端口（与 deploy-linux.sh 一致）
            ufw delete allow 8120/tcp >/dev/null 2>&1 || true
            ufw allow "${_lv_port_ufw}/tcp" >/dev/null 2>&1 || true
            ok "已放行防火墙 ${_lv_port_ufw} 端口（ufw 规则已添加；旧 8120 规则已清理；若 ufw 未启用则该规则暂未生效）"
        else
            warn "无域名部署：请确认云/系统防火墙放行 TCP ${_lv_port_ufw}，否则 http://<IP>:${_lv_port_ufw}/ 不可达。"
        fi
    fi
fi

# ---------------- 收尾 ----------------
echo
# 审计 X-07：收尾核对代码是否在本次更新期间被换过（例如并发 pull / 半途更新），
# 不一致则明确报错，避免把「新库结构 + 旧代码」当成一次干净升级。
if [[ -n "${CODE_HEAD_BEFORE:-}" ]]; then
    _code_head_now=""
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        _code_head_now="$(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo '')"
    elif [[ -n "${SOURCE_DIR:-}" && -d "$SOURCE_DIR/.git" ]]; then
        _code_head_now="$(git -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo '')"
    fi
    if [[ -n "$_code_head_now" && "$_code_head_now" != "$CODE_HEAD_BEFORE" ]]; then
        warn "代码在本轮更新期间发生了变化：开始时 ${CODE_HEAD_BEFORE:0:12} → 现在 ${_code_head_now:0:12}"
        warn "若这不是你预期的（例如另一处并发 pull），请核对部署目录内容后再重启服务。"
    else
        echo "  代码版本：    ${CODE_HEAD_BEFORE:0:12}（本轮未变）"
    fi
fi
ok "更新完成！"
echo "  部署目录：    $INSTALL_DIR"
echo "  备份位置：    $BACKUP_DIR"
echo "  后端状态：    systemctl status gipfel"
echo "  日志查看器：  systemctl status gipfel-logviewer"
# 网站地址提示：有域名显示域名；纯 IP 部署显示公网 IP（优先 --public-ip，其次探测），
# 不再用 hostname -I 首地址（通常为内网 IP，对用户访问无意义）。
if [[ -n "$DOMAIN" ]]; then
    echo "  网站：        http://${DOMAIN}/"
    echo "  健康检查：    curl -sS http://${DOMAIN}:8000/api/health"
else
    if [[ -z "$PUBLIC_IP" ]]; then
        PUBLIC_IP="$(_probe_public_ip)" || true
    fi
    if [[ -n "$PUBLIC_IP" ]]; then
        echo "  网站：        http://${PUBLIC_IP}/"
        echo "  健康检查：    curl -sS http://${PUBLIC_IP}:8000/api/health"
    else
        echo "  网站：        http://<公网IP>/（未能自动探测公网 IP，请用云控制台查询后访问）"
        echo "  健康检查：    curl -sS http://127.0.0.1:8000/api/health"
    fi
fi
echo "  日志：        journalctl -u gipfel -f   /   tail -F $INSTALL_DIR/backend/logs/app.log"

# ============================================================
# 部署自检：把「线上实际生效的状态」直接打出来。
# 目的——**不需要人工去查 .env / 进程环境 / 监听端口**。任何一项不对，这里就说清是什么、怎么修。
# 起因：多次出现「改了配置但没生效」的排查拉锯（.env 遗留值、改完没重启、端口没监听……
#       每一样都得人工逐个去猜）。脚本既然知道期望值，就该自己核对并报告。
# 注意：本段只读不写；任何一项失败都**不中止**（升级主体已完成，自检只做告知）。
# ============================================================
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  部署自检（只读，不影响上面已完成的升级）"
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
            echo "         → 将按请求 Host 推导；若 Host 是 IP 会得到 http://<IP>:8120/（CF 代理下打不开）"
            echo "         修：重跑本脚本（会自动写该变量），或检查 .env 是否被手工改回"
            _SELFCHECK_WARN=1
        fi
    else
        echo "  [WARN] 读不到 gipfel 进程环境（未运行或无权读取）→ systemctl status gipfel"
        _SELFCHECK_WARN=1
    fi
fi

# 2) 监听端口：80/443 必有；8443 视形态；8121 是日志查看器内部端口
if command -v ss >/dev/null 2>&1; then
    echo "  监听端口："
    ss -lntp 2>/dev/null | grep -E ':(80|443|8121|8443)\b' | sed 's/^/    /' || echo "    （未匹配到 80/443/8121/8443）"
    if [[ -n "$LOGVIEWER_TLS_PORT" ]] && ! ss -lnt 2>/dev/null | grep -qE ":${LOGVIEWER_TLS_PORT}\b"; then
        echo "  [WARN] 已要求日志查看器监听 ${LOGVIEWER_TLS_PORT}，但它没有在监听"
        echo "         → 多半是 nginx 未 reload 或缺少该 server 块：sudo nginx -t && sudo systemctl reload nginx"
        _SELFCHECK_WARN=1
    fi
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

# 明确退出，避免依赖上一条命令的偶然退出码
exit 0
