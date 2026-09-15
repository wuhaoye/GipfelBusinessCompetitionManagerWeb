#!/usr/bin/env bash
# 一键诊断：日志查看器打不开（400 / 403 / 连不上）
# 在服务器上执行：bash gipfel-logviewer-diag.sh [INSTALL_DIR] [域名]
#   INSTALL_DIR 可传（默认 /opt/gipfel），不再把安装目录写死在各处；
#   域名可传以精确检查「域名部署」形态，不传则从 .env 的 DJANGO_ALLOWED_HOSTS 里猜第一个非 IP 条目。
#
# 审计 X-14（master 合并后重做 —— master 把这支脚本整篇重写为「域名形态 + CSRF + 证书」诊断，
# 但重写版丢掉了 X-14 的 5 项加固，本文件把两者合并）：
#   ① 改前第 6 行用 `grep -E "^(…|LOGVIEWER_SECRET_KEY|…)="` 把 **LOGVIEWER_SECRET_KEY 原文**
#      打到 stdout —— 该密钥可自签日志查看器/`/admin` 防直连令牌，任何能读到终端输出、
#      CI 日志、会话记录的人都能据此绕过 nginx 直连。现在一律掩码显示（只留前 4 位与长度）。
#   ② 所有 curl 走统一的 CURL_OPTS（`--connect-timeout` + `--max-time`）—— 改前无超时，
#      网络异常时脚本会永久挂住。
#   ③ 端口不再硬编码：从 .env 的 `LOG_VIEWER_PORT` 读取（与 nginx/ufw 同源），缺省兜底 8120。
#   ④ `sudo -n` 失败时明确提示"需要 NOPASSWD 或手动查看"，不再静默无输出。
#   ⑤ 安装目录可传入（默认 /opt/gipfel），不再写死。
set +e

INSTALL_DIR="${1:-/opt/gipfel}"
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/backend/.env}"
CURL_OPTS=(--connect-timeout 3 --max-time 6 -s -o /dev/null)

have_sudo_n() { sudo -n true 2>/dev/null; }

# 掩码：只显示前 4 位与长度，绝不输出密钥原文
mask() {
    local v="$1"
    local n=${#v}
    if [[ -z "$v" ]]; then printf '<空>'; return; fi
    if (( n <= 8 )); then printf '****(len=%d)' "$n"; else printf '%s****(len=%d)' "${v:0:4}" "$n"; fi
}

# 日志查看器公网监听端口：与 nginx/ufw 同源，读 .env 的 LOG_VIEWER_PORT
get_lv_port() {
    local p=""
    if [[ -f "$ENV_FILE" ]]; then
        p=$(grep -E '^[[:space:]]*LOG_VIEWER_PORT=' "$ENV_FILE" 2>/dev/null | tail -1 \
            | cut -d= -f2- | tr -d '[:space:]' | sed -E "s/^['\"]//; s/['\"]$//")
    fi
    if ! [[ "$p" =~ ^[0-9]+$ ]] || (( p < 1 || p > 65535 )); then
        p=8120
    fi
    printf '%s' "$p"
}
LV_PORT="${LV_PORT:-$(get_lv_port)}"
LV_PORT="${LV_PORT:-8120}"

echo "======================================================"
echo " 安装目录: $INSTALL_DIR"
echo " 日志查看器公网端口: $LV_PORT（来自 $ENV_FILE 的 LOG_VIEWER_PORT）"
echo "======================================================"
echo "================ 1. .env 当前关键变量（密钥已掩码）================"
if [[ -f "$ENV_FILE" ]]; then
    while IFS= read -r line; do
        key="${line%%=*}"
        val="${line#*=}"
        case "$key" in
            LOGVIEWER_SECRET_KEY) echo "  ${key}=$(mask "$val")  # 已掩码（改前会打印原文）" ;;
            *) echo "  $line" ;;
        esac
    done < <(grep -E "^(LOG_VIEWER_PUBLIC_URL|DJANGO_ALLOWED_HOSTS|LOGVIEWER_ALLOWED_HOSTS|LOGVIEWER_SECRET_KEY|LOG_VIEWER_PORT)=" "$ENV_FILE" 2>/dev/null)
else
    echo "  [warn] $ENV_FILE 缺失"
fi
echo ""
echo "================ 2. 服务单元版本（确认拉到最新）================"
if have_sudo_n; then
    sudo -n cat "$INSTALL_DIR/deploy/logviewer.service" 2>/dev/null | grep -E "(ExecStart|WorkingDirectory|RuntimeDirectory|ReadWritePaths)" | head -6
else
    echo "  [warn] 无 NOPASSWD sudo（sudo -n 不可用）：请手动执行 cat $INSTALL_DIR/deploy/logviewer.service"
    grep -E "(ExecStart|WorkingDirectory|RuntimeDirectory|ReadWritePaths)" \
        "$INSTALL_DIR/deploy/logviewer.service" 2>/dev/null | head -6 \
        || echo "  [warn] 该文件不可读"
fi
echo ""
echo "================ 3. 服务运行状态 ================"
systemctl is-active gipfel-logviewer && echo "  [ok] gipfel-logviewer 运行中" || echo "  [FAIL] gipfel-logviewer 未运行"
PID=$(systemctl show -p MainPID gipfel-logviewer 2>/dev/null | cut -d= -f2)
if [[ -n "$PID" && "$PID" != "0" ]]; then
    echo "  PID=$PID，启动时间：$(ps -o lstart= -p "$PID" 2>/dev/null)"
fi
ss -lntp 2>/dev/null | grep 8121 || echo "  [warn] 没有进程监听 8121（daphne 没起来？）"
echo ""

AH_LINE=$(grep -E "^DJANGO_ALLOWED_HOSTS=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2)
echo "================ 4. 白名单内容 ================"
echo "  DJANGO_ALLOWED_HOSTS = ${AH_LINE:-<空>}"
PUB_IP=$(printf '%s' "$AH_LINE" | cut -d, -f1)

# 推断域名：优先命令行参数，其次 DJANGO_ALLOWED_HOSTS 里第一个非 IPv4 条目
DOMAIN="${2:-}"
if [[ -z "$DOMAIN" ]]; then
  DOMAIN=$(printf '%s' "$AH_LINE" | tr ',' '\n' | grep -vE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' | grep -vE '^(localhost|127\.0\.0\.1|::1)$' | head -1)
fi
echo "  识别到的域名: ${DOMAIN:-<无（纯 IP 形态）>}"
echo ""

echo "================ 5. 向进程探测 Host（400 = 白名单问题）================"
echo "  [a] 主站域名（应 302/200 而非 400）："
[[ -n "$DOMAIN" ]] && curl "${CURL_OPTS[@]}" -w "    Host: ${DOMAIN} -> HTTP %{http_code}\n" -H "Host: ${DOMAIN}" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无域名）"
echo "  [b] 日志查看器子域 log.<域名>（★ 400 即域名模式白名单缺项）："
[[ -n "$DOMAIN" ]] && curl "${CURL_OPTS[@]}" -w "    Host: log.${DOMAIN} -> HTTP %{http_code}\n" -H "Host: log.${DOMAIN}" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无域名）"
echo "  [c] log.<域名>:80（nginx 实际透传的带默认端口形式）："
[[ -n "$DOMAIN" ]] && curl "${CURL_OPTS[@]}" -w "    Host: log.${DOMAIN}:80 -> HTTP %{http_code}\n" -H "Host: log.${DOMAIN}:80" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无域名）"
echo "  [d] 纯 IP + 实际端口 ${LV_PORT}（应 302/200 而非 400）："
[[ -n "$PUB_IP" ]] && curl "${CURL_OPTS[@]}" -w "    Host: ${PUB_IP}:${LV_PORT} -> HTTP %{http_code}\n" -H "Host: ${PUB_IP}:${LV_PORT}" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无公网 IP）"
echo "  [e] 经 nginx 公网口访问（应 200/302）："
curl "${CURL_OPTS[@]}" -w "    127.0.0.1:${LV_PORT} -> HTTP %{http_code}\n" "http://127.0.0.1:${LV_PORT}/"
echo "  [f] 恶意 Host（应 400，证明白名单仍生效）："
curl "${CURL_OPTS[@]}" -w "    Host: evil.com -> HTTP %{http_code}\n" -H "Host: evil.com" http://127.0.0.1:8121/
echo ""

echo "================ 6. 进程实际生效的 CSRF_TRUSTED_ORIGINS（403 = CSRF 问题）================"
# 以同一份 .env 直接导入 settings，打印真实生效值（而非猜测）
PY="${INSTALL_DIR}/backend/.venv/bin/python"
if [[ -x "$PY" ]]; then
  ( set -a; . "$ENV_FILE" 2>/dev/null; set +a
    "$PY" -c "
import sys
sys.path.insert(0, '${INSTALL_DIR}/backend/logviewer')
try:
    import logviewer.settings as s
    print('  ALLOWED_HOSTS        =', s.ALLOWED_HOSTS)
    print('  CSRF_TRUSTED_ORIGINS =', s.CSRF_TRUSTED_ORIGINS)
    dom = '${DOMAIN}'
    if dom:
        want = ['http://log.' + dom, 'https://log.' + dom]
        missing = [w for w in want if w not in s.CSRF_TRUSTED_ORIGINS]
        if missing:
            print('  [FAIL] 缺少 CSRF 来源: ' + ', '.join(missing))
        else:
            print('  [ok] log.<域名> 的 http/https 来源都在')
except Exception as e:
    print('  [warn] 无法导入 settings:', e)
" 2>&1 )
else
  echo "  [warn] 找不到 $PY，跳过"
fi
echo ""

echo "================ 7. nginx 与证书 ================"
echo "  443 上是否有该子域的 server 块："
if have_sudo_n; then
    sudo -n nginx -T 2>/dev/null | grep -B3 -A2 "server_name log\." | head -20 || echo "    （无匹配）"
    echo "  证书覆盖的域名："
    sudo -n certbot certificates 2>/dev/null | grep -E "Certificate Name|Domains" | head -10 || echo "    （未安装 certbot）"
else
    echo "  [warn] 无 NOPASSWD sudo（sudo -n 不可用）：请手动执行 sudo nginx -T | grep -A2 'server_name log\.'"
    echo "  [warn] 无 NOPASSWD sudo（sudo -n 不可用）：请手动执行 sudo certbot certificates"
fi
echo ""

echo "================ 8. 失败时的日志线索 ================"
echo "  [tip] 若 [b]/[c] 为 400 → 白名单缺 log.<域名>；若页面 403 → CSRF 来源缺项。"
echo "  最近 30 行（过滤关键错误）："
if have_sudo_n; then
    sudo -n journalctl -u gipfel-logviewer -n 30 --no-pager 2>/dev/null | grep -E "(DisallowedHost|ALLOWED_HOSTS|Invalid HTTP_HOST|CSRF|Error)" | tail -10
else
    journalctl -u gipfel-logviewer -n 30 --no-pager 2>/dev/null | grep -E "(DisallowedHost|ALLOWED_HOSTS|Invalid HTTP_HOST|CSRF|Error)" | tail -10 \
        || echo "  [warn] 无权限读取 journal：请手动执行 sudo journalctl -u gipfel-logviewer -n 30"
fi
