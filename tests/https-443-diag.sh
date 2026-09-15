#!/usr/bin/env bash
# 一键诊断：https 报 521（Cloudflare 连不上源站）
#
# 在服务器上执行：bash https-443-diag.sh [域名]
# 判据核心：CF 能连 80 却连不上 443 → 问题只在源站 443（没监听 / 被挡），
#          与证书内容、CF 证书配置都无关（那些会表现为 525/526）。
set +e
DOMAIN="${1:-}"
ENV_FILE="${ENV_FILE:-/opt/gipfel/backend/.env}"
VHOST="/etc/nginx/sites-available/gipfel.conf"
CERT_DIR="${CERT_DIR:-/etc/ssl/cloudflare}"
[ -n "$DOMAIN" ] || DOMAIN="$(grep -oE '^DJANGO_ALLOWED_HOSTS=[^,]*' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
[ -n "$DOMAIN" ] || DOMAIN="<你的域名>"

VERDICT=()

echo "================ 1. 80 / 443 到底有没有在监听 ================"
LISTEN="$(ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null)"
printf '%s\n' "$LISTEN" | grep -E ':(80|443)\b' || echo "  （80/443 都没有监听？）"
if printf '%s\n' "$LISTEN" | grep -qE ':443\b'; then
  echo "  [ok] 有进程在监听 443"
else
  echo "  [FAIL] ★ 没有任何进程监听 443 —— 这就是 521 的直接原因"
  VERDICT+=("443 无监听：nginx 未加载含 443 的配置（或 nginx 没起来/没 reload）")
fi
echo ""

echo "================ 2. 渲染产物里是否有生效的 443 块 ================"
if [[ -f "$VHOST" ]]; then
  echo "  文件：$VHOST"
  echo "  ---- listen 相关 ----"
  grep -nE '^[[:space:]]*listen' "$VHOST" || echo "    （无 listen 行？文件可能为空）"
  echo "  ---- ssl_certificate 相关 ----"
  grep -nE '^[[:space:]]*ssl_certificate' "$VHOST" || echo "    （无 ssl_certificate —— 443 块仍是注释态）"
  if grep -qE '^[[:space:]]*listen[[:space:]]+443' "$VHOST"; then
    echo "  [ok] 产物里有生效的 443 块"
  else
    echo "  [FAIL] ★ 产物里没有生效的 443 块（仍是注释态）"
    VERDICT+=("vhost 未启用 443：没跑 --origin-cert，或改错了文件（改的是仓库模板 deploy/nginx-gipfel.conf，不生效）")
  fi
else
  echo "  [FAIL] 找不到 $VHOST"
  VERDICT+=("找不到 vhost 文件：nginx 可能从未被本项目的脚本配置过")
fi
echo ""

echo "================ 3. nginx 状态与配置校验 ================"
echo -n "  nginx -t ："
if sudo -n nginx -t 2>&1 | tail -2; then :; else echo "    （需要 sudo；请手动执行 sudo nginx -t）"; fi
echo -n "  nginx 运行："; systemctl is-active nginx 2>/dev/null || echo "unknown"
echo "  已加载的 443 配置（nginx -T）："
sudo -n nginx -T 2>/dev/null | grep -nE 'listen[[:space:]]+443|ssl_certificate' | head -10 \
  || echo "    （需要 sudo 才能读；请手动执行 sudo nginx -T | grep -E 'listen 443|ssl_certificate'）"
echo "  [提示] 若第 2 步显示有 443、第 1 步却没有监听 → 配置改完没 reload：sudo systemctl reload nginx"
echo ""

echo "================ 4. 证书文件 ================"
echo "  目录 $CERT_DIR："
ls -l "$CERT_DIR" 2>/dev/null || echo "    （目录不存在）"
for f in "${CERT_DIR}/${DOMAIN}.pem" "${CERT_DIR}/${DOMAIN}.key"; do
  if [[ -s "$f" ]]; then
    echo "  [ok] 存在：$f"
  else
    echo "  [FAIL] 缺失或为空：$f"
    VERDICT+=("证书文件缺失/为空：$f（脚本按 <目录>/<域名>.pem|.key 推导）")
  fi
done
if [[ -s "${CERT_DIR}/${DOMAIN}.pem" ]]; then
  echo "  ---- 证书内容（issuer 应为 CloudFlare Origin SSL Certificate Authority）----"
  openssl x509 -in "${CERT_DIR}/${DOMAIN}.pem" -noout -subject -issuer -dates 2>/dev/null \
    || echo "    （openssl 解析失败：文件可能不是 PEM 证书）"
  echo "  ---- 证书是否覆盖该域名 ----"
  openssl x509 -in "${CERT_DIR}/${DOMAIN}.pem" -noout -text 2>/dev/null \
    | grep -A1 'Subject Alternative Name' | tail -1 || true
fi
echo ""

echo "================ 5. 防火墙（443 是否放行）================"
if command -v ufw >/dev/null 2>&1; then
  sudo -n ufw status verbose 2>/dev/null || ufw status 2>/dev/null || echo "    （需要 sudo）"
  if sudo -n ufw status 2>/dev/null | grep -q '443'; then
    echo "  [ok] ufw 规则里有 443"
  else
    echo "  [warn] ufw 规则里看不到 443 —— 若 ufw 已启用，需 sudo ufw allow 443/tcp"
    VERDICT+=("ufw 未放行 443（sudo ufw allow 443/tcp）；云安全组入方向也要放行 TCP 443")
  fi
else
  echo "  未安装 ufw"
fi
echo "  iptables INPUT（前 20 行）："
sudo -n iptables -L INPUT -n --line-numbers 2>/dev/null | head -20 || echo "    （需要 sudo）"
echo ""

echo "================ 6. 本机自测（绕开 Cloudflare）================"
echo "  [a] 直连本机 443："
curl -skI --max-time 8 --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/" 2>&1 | head -3 \
  || echo "    连接失败 → 本机 443 确实没人应答"
echo "  [b] 直连本机 80（对照，应 200/302）："
curl -sI --max-time 8 --resolve "${DOMAIN}:80:127.0.0.1" "http://${DOMAIN}/" 2>&1 | head -3
echo ""

echo "================ 7. 结论 ================"
if [[ ${#VERDICT[@]} -eq 0 ]]; then
  echo "  未发现本机层面的问题。若 CF 仍报 521，请检查："
  echo "    - 云控制台安全组入方向是否放行 TCP 443（脚本管不到云侧）"
  echo "    - 源站前面是否有别的 NAT/路由未转发 443"
  echo "    - CF 面板 SSL/TLS 模式（应为 Full (strict)；Flexible 会走 80，与 521 不符）"
else
  echo "  按顺序处理："
  i=1
  for v in "${VERDICT[@]}"; do echo "   $i) $v"; i=$((i+1)); done
  echo ""
  echo "  ★ 最省事的修法（由脚本渲染 443 + reload）："
  echo "     cd /opt/GipfelBusinessCompetitionManagerWeb && sudo git pull"
  echo "     sudo bash scripts/update-from-github.sh \\"
  echo "       --source-dir /opt/GipfelBusinessCompetitionManagerWeb \\"
  echo "       --install-dir /opt/gipfel --with-nginx --domain ${DOMAIN} --origin-cert"
  echo "     sudo ufw allow 443/tcp   # 云安全组也要放行 443"
fi
