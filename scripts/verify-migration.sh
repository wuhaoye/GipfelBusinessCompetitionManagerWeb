#!/usr/bin/env bash
# ============================================================
# Gipfel 迁移验证脚本
# 用途：检查服务器迁移后的服务状态
#
# 使用方法：
#   bash scripts/verify-migration.sh [install-dir]
# ============================================================

set -euo pipefail

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

# 审计 X-01：改前是 `((PASS++))` / `((FAIL++))` / `((WARN++))` —— 在 `set -e` 下，
# 后置自增表达式的**返回值是自增前的旧值**，第一次自增时旧值为 0 ⇒ 算术命令返回非零
# ⇒ bash 立即中止脚本。结果是「第一项检查通过后脚本就退出，且退出码 0」，
# 调用方/CI 会把它误判为「迁移成功」。
# 现在改用 POSIX 赋值形式（赋值永远返回 0），计数照常累加。
check_pass() { echo -e "  ${GREEN}✓${NC} $*"; PASS=$((PASS + 1)); }
check_fail() { echo -e "  ${RED}✗${NC} $*"; FAIL=$((FAIL + 1)); }
check_warn() { echo -e "  ${YELLOW}⚠${NC} $*"; WARN=$((WARN + 1)); }

INSTALL_DIR="${1:-/opt/gipfel}"

echo "=========================================="
echo "Gipfel 迁移验证"
echo "安装目录: $INSTALL_DIR"
echo "=========================================="
echo ""

# ---------- 文件检查 ----------
echo "[1/5] 检查文件完整性..."

if [[ -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
    size=$(stat -f%z "$INSTALL_DIR/backend/db.sqlite3" 2>/dev/null || stat -c%s "$INSTALL_DIR/backend/db.sqlite3" 2>/dev/null || echo "0")
    if [[ "$size" -gt 0 ]]; then
        check_pass "数据库文件存在 ($(numfmt --to=iec $size 2>/dev/null || echo "$size bytes"))"
    else
        check_fail "数据库文件为空"
    fi
    # 审计 X-09：改前只测**文件大小** —— 一个损坏的 SQLite（例如复制到一半）或只有空
    # schema 的库同样 size>0，会被判为「迁移成功」。这里真正打开库并查一张业务表。
    if command -v python3 >/dev/null 2>&1; then
        if db_msg=$(python3 - "$INSTALL_DIR/backend/db.sqlite3" <<'PY' 2>&1
import sqlite3, sys
path = sys.argv[1]
try:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        ).fetchall()
        if not rows:
            print("EMPTY_SCHEMA")
            sys.exit(2)
        # 业务库至少要有 Django 迁移表与一个业务表
        names = {r[0] for r in rows}
        if "django_migrations" not in names:
            print("NO_DJANGO_MIGRATIONS")
            sys.exit(3)
        n = con.execute("select count(*) from django_migrations").fetchone()[0]
        print(f"OK tables={len(names)} migrations={n}")
    finally:
        con.close()
except sqlite3.DatabaseError as exc:
    print(f"BROKEN: {exc}")
    sys.exit(4)
PY
        ); then
            check_pass "数据库可打开且 schema 非空 ($db_msg)"
        else
            check_fail "数据库不可用：$db_msg"
        fi
    else
        check_warn "python3 未安装，无法校验数据库结构（只检查了文件大小）"
    fi
else
    check_fail "数据库文件不存在: $INSTALL_DIR/backend/db.sqlite3"
fi

if [[ -f "$INSTALL_DIR/backend/.env" ]]; then
    check_pass "环境配置文件存在"
else
    check_fail "环境配置文件不存在: $INSTALL_DIR/backend/.env"
fi

if [[ -d "$INSTALL_DIR/backend/uploads" ]]; then
    count=$(find "$INSTALL_DIR/backend/uploads" -type f 2>/dev/null | wc -l)
    check_pass "上传目录存在 ($count 个文件)"
else
    check_warn "上传目录不存在（可能是新安装）"
fi

if [[ -d "$INSTALL_DIR/frontend-dist" ]]; then
    check_pass "前端构建产物存在"
else
    check_fail "前端构建产物不存在"
fi

echo ""

# ---------- 服务检查 ----------
echo "[2/5] 检查系统服务..."

if systemctl is-active --quiet gipfel 2>/dev/null; then
    check_pass "gipfel 服务运行中（daphne :8000，承载 /socket.io/）"
else
    check_warn "gipfel 服务未运行"
fi

# C1-a：/api/ 的实际承载者是多 worker 的 gunicorn(WSGI)，与 daphne 是两个独立 unit。
# 只检查 gipfel：会出现"daphne 活着、WSGI 没起来"的迁移结果被判为成功，
# 而现场表现是登录/接口全 502（实时推送却正常），排查方向会被完全带偏。
if systemctl is-active --quiet gipfel-wsgi 2>/dev/null; then
    check_pass "gipfel-wsgi 服务运行中（gunicorn，承载 /api/、/admin/）"
else
    check_warn "gipfel-wsgi 服务未运行（/api/ 会 502）"
fi

if systemctl is-active --quiet gipfel-logviewer 2>/dev/null; then
    check_pass "gipfel-logviewer 服务运行中"
else
    check_warn "gipfel-logviewer 服务未运行"
fi

if systemctl is-active --quiet nginx 2>/dev/null; then
    check_pass "nginx 服务运行中"
else
    check_warn "nginx 服务未运行"
fi

echo ""

# ---------- API 检查 ----------
echo "[3/5] 检查 API 响应..."

if command -v curl >/dev/null 2>&1; then
    # C1-a：/api/ 现在由 gunicorn(WSGI) 承载，端口取 backend/.env 的 GIPFEL_WSGI_PORT（默认 8002，
    # 与 deploy/gipfel-wsgi.service 的 Environment 默认值、nginx upstream gipfel_django 同源）。
    wsgi_port=""
    if [[ -f "$INSTALL_DIR/backend/.env" ]]; then
        wsgi_port=$(grep -E '^[[:space:]]*GIPFEL_WSGI_PORT[[:space:]]*=' "$INSTALL_DIR/backend/.env" \
            | tail -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//' || true)
    fi
    wsgi_port="${wsgi_port:-8002}"
    response=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${wsgi_port}/api/health" 2>/dev/null || echo "000")
    if [[ "$response" == "200" ]]; then
        check_pass "WSGI(gunicorn :${wsgi_port}) API 健康检查通过 (HTTP 200)"
    else
        check_fail "WSGI(gunicorn :${wsgi_port}) API 健康检查失败 (HTTP $response)"
    fi

    # C1-a：daphne(ASGI) 的职责是 Socket.IO，用 HTTP 握手探活（期望 200）——
    # 只看 systemctl 状态无法区分"进程活着但 ASGI 应用起不来"。
    io_resp=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://127.0.0.1:8000/socket.io/?EIO=4&transport=polling" 2>/dev/null || echo "000")
    if [[ "$io_resp" == "200" ]]; then
        check_pass "daphne(:8000) Socket.IO 握手通过 (HTTP 200)"
    else
        check_fail "daphne(:8000) Socket.IO 握手失败 (HTTP $io_resp) —— 实时推送不可用"
    fi

    # 审计 X-09：改前只验证主应用的 `/api/health` —— 日志查看器（`gipfel-logviewer`）
    # 只有 systemctl 状态检查，进程活着但 HTTP 不可用时会被判为「迁移成功」。
    # 端口取 backend/.env 的 LOG_VIEWER_PORT（与 deploy-linux.sh 的 nginx 监听口同源）。
    lv_port=""
    if [[ -f "$INSTALL_DIR/backend/.env" ]]; then
        lv_port=$(grep -E '^[[:space:]]*LOG_VIEWER_PORT[[:space:]]*=' "$INSTALL_DIR/backend/.env" \
            | tail -1 | sed -E 's/^[^=]*=[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//' || true)
    fi
    lv_port="${lv_port:-8120}"
    lv_response=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${lv_port}/api/health" 2>/dev/null || echo "000")
    if [[ "$lv_response" == "200" ]]; then
        check_pass "日志查看器健康检查通过 (HTTP 200, 端口 $lv_port)"
    else
        check_fail "日志查看器健康检查失败 (HTTP $lv_response, 端口 $lv_port)"
    fi
else
    check_warn "curl 未安装，跳过 API 检查"
fi

echo ""

# ---------- 权限检查 ----------
echo "[4/5] 检查文件权限..."

if [[ -f "$INSTALL_DIR/backend/.env" ]]; then
    perms=$(stat -c%a "$INSTALL_DIR/backend/.env" 2>/dev/null || echo "unknown")
    if [[ "$perms" == "600" || "$perms" == "640" ]]; then
        check_pass ".env 文件权限正确 ($perms)"
    else
        check_warn ".env 文件权限较宽松 ($perms)，建议设置为 600"
    fi
fi

if [[ -d "$INSTALL_DIR/backend" ]]; then
    owner=$(stat -c%U "$INSTALL_DIR/backend" 2>/dev/null || echo "unknown")
    if [[ "$owner" == "gipfel" ]]; then
        check_pass "backend 目录所有者正确 (gipfel)"
    else
        check_warn "backend 目录所有者为 $owner（建议为 gipfel）"
    fi
fi

echo ""

# ---------- 配置检查 ----------
echo "[5/5] 检查配置文件..."

if [[ -f "/etc/nginx/sites-enabled/gipfel.conf" ]]; then
    check_pass "nginx 配置已启用"
else
    check_warn "nginx 配置未启用（可能需要创建软链接）"
fi

if [[ -f "/etc/systemd/system/gipfel.service" ]]; then
    check_pass "systemd 服务文件已安装"
else
    check_warn "systemd 服务文件未安装"
fi

echo ""

# ---------- 总结 ----------
echo "=========================================="
echo "验证结果"
echo "=========================================="
echo -e "  ${GREEN}通过: $PASS${NC}"
echo -e "  ${RED}失败: $FAIL${NC}"
echo -e "  ${YELLOW}警告: $WARN${NC}"
echo ""

if [[ $FAIL -gt 0 ]]; then
    echo -e "${RED}存在失败项，请检查并修复！${NC}"
    exit 1
elif [[ $WARN -gt 0 ]]; then
    echo -e "${YELLOW}存在警告项，建议检查。${NC}"
    exit 0
else
    echo -e "${GREEN}所有检查通过！迁移成功！${NC}"
    exit 0
fi
