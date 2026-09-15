#!/usr/bin/env bash
# 一键诊断：日志查看器打不开（400 / 403 / 连不上）
# 在服务器上执行：bash gipfel-logviewer-diag.sh [域名]
#   传域名可精确检查「域名部署」形态；不传则从 .env 的 DJANGO_ALLOWED_HOSTS 里猜第一个非 IP 条目。
set +e
ENV_FILE="${ENV_FILE:-/opt/gipfel/backend/.env}"
INSTALL_DIR="${INSTALL_DIR:-/opt/gipfel}"

echo "================ 1. .env 当前关键变量 ================"
grep -E "^(LOG_VIEWER_PUBLIC_URL|DJANGO_ALLOWED_HOSTS|LOGVIEWER_ALLOWED_HOSTS|LOGVIEWER_SECRET_KEY|LOG_VIEWER_PORT)=" "$ENV_FILE" 2>/dev/null || echo "  [warn] $ENV_FILE 缺失"
echo ""
echo "================ 2. 服务单元版本（确认拉到最新）================"
sudo -n cat "$INSTALL_DIR/deploy/logviewer.service" 2>/dev/null | grep -E "(ExecStart|WorkingDirectory)" | head -2
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
DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
  DOMAIN=$(printf '%s' "$AH_LINE" | tr ',' '\n' | grep -vE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' | grep -vE '^(localhost|127\.0\.0\.1|::1)$' | head -1)
fi
echo "  识别到的域名: ${DOMAIN:-<无（纯 IP 形态）>}"
echo ""

echo "================ 5. 向进程探测 Host（400 = 白名单问题）================"
echo "  [a] 主站域名（应 302/200 而非 400）："
[[ -n "$DOMAIN" ]] && curl -s -o /dev/null -w "    Host: ${DOMAIN} -> HTTP %{http_code}\n" -H "Host: ${DOMAIN}" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无域名）"
echo "  [b] 日志查看器子域 log.<域名>（★ 400 即域名模式白名单缺项）："
[[ -n "$DOMAIN" ]] && curl -s -o /dev/null -w "    Host: log.${DOMAIN} -> HTTP %{http_code}\n" -H "Host: log.${DOMAIN}" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无域名）"
echo "  [c] log.<域名>:80（nginx 实际透传的带默认端口形式）："
[[ -n "$DOMAIN" ]] && curl -s -o /dev/null -w "    Host: log.${DOMAIN}:80 -> HTTP %{http_code}\n" -H "Host: log.${DOMAIN}:80" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无域名）"
echo "  [d] 纯 IP 形态（应 302/200 而非 400）："
[[ -n "$PUB_IP" ]] && curl -s -o /dev/null -w "    Host: ${PUB_IP}:8120 -> HTTP %{http_code}\n" -H "Host: ${PUB_IP}:8120" http://127.0.0.1:8121/ \
                   || echo "    （跳过：无公网 IP）"
echo "  [e] 恶意 Host（应 400，证明白名单仍生效）："
curl -s -o /dev/null -w "    Host: evil.com -> HTTP %{http_code}\n" -H "Host: evil.com" http://127.0.0.1:8121/
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
sudo -n nginx -T 2>/dev/null | grep -B3 -A2 "server_name log\." | head -20 || echo "    （无 sudo 或无匹配）"
echo "  证书覆盖的域名："
sudo -n certbot certificates 2>/dev/null | grep -E "Certificate Name|Domains" | head -10 || echo "    （未安装 certbot 或需要 sudo）"
echo ""

echo "================ 8. 失败时的日志线索 ================"
echo "  [tip] 若 [b]/[c] 为 400 → 白名单缺 log.<域名>；若页面 403 → CSRF 来源缺项。"
echo "  最近 30 行（过滤关键错误）："
sudo -n journalctl -u gipfel-logviewer -n 30 --no-pager 2>/dev/null | grep -E "(DisallowedHost|ALLOWED_HOSTS|Invalid HTTP_HOST|CSRF|Error)" | tail -10
