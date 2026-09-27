"""X-24：deploy-linux.sh 把"降级继续"的分支计入问题数，收尾汇总并据此决定退出码（CRLF 保持）。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "deploy-linux.sh"

PATCHES: list[tuple[str, str]] = []

# 1) 计数与 problem()
PATCHES.append((
    """err()   { printf "\\033[31m[ERROR]\\033[0m %s\\n" "$*"; exit 1; }
""",
    """err()   { printf "\\033[31m[ERROR]\\033[0m %s\\n" "$*"; exit 1; }

# 审计 X-24：改前所有"降级继续"的分支只 `warn`，收尾照样 `ok "部署完成！"` 并以 0 退出。
# 于是一个"日志查看器端口没监听 / nginx reload 失败、站点仍是旧配置"的部署，在 CI 里
# 被判定为成功。现在把这些分支计入 DEPLOY_PROBLEMS，收尾汇总；非 0 时默认以非 0 退出
# （`--allow-partial` 可显式接受当前状态，恢复旧的"总是 0"行为）。
DEPLOY_PROBLEMS=0
ALLOW_PARTIAL=0
problem() { DEPLOY_PROBLEMS=$((DEPLOY_PROBLEMS + 1)); warn "$@"; }
""",
))

# 2) usage
PATCHES.append((
    """  --print-seed-password        在**交互终端**（stdout 为 TTY）上显示初始管理员口令；
                               默认不显示，请自行 `sudo grep ^SEED_ADMIN_PASSWORD= <安装目录>/backend/.env`
  -h, --help                   显示本帮助""",
    """  --print-seed-password        在**交互终端**（stdout 为 TTY）上显示初始管理员口令；
                               默认不显示，请自行 `sudo grep ^SEED_ADMIN_PASSWORD= <安装目录>/backend/.env`
  --allow-partial              即使有检查未通过也以退出码 0 结束（默认：有问题即非 0 退出，
                               便于 CI/自动化判定"部署是否真的成功"）
  -h, --help                   显示本帮助""",
))

# 3) 参数解析
PATCHES.append((
    """        --print-seed-password) PRINT_SEED_PASSWORD=1; shift ;;""",
    """        --print-seed-password) PRINT_SEED_PASSWORD=1; shift ;;
        --allow-partial)       ALLOW_PARTIAL=1; shift ;;""",
))

# 4) nginx 端口预检 + reload/start 失败入账 + 功能探针
PATCHES.append((
    """    nginx -t || err "nginx -t 失败，请修正"
    # 全新服务器 nginx 可能尚未启动，reload 对未运行服务会失败；按状态选择 start / reload
    systemctl enable nginx 2>/dev/null || true
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
        ok "nginx 配置已 reload"
    else
        systemctl start nginx
        ok "nginx 已启动"
    fi
""",
    """    nginx -t || err "nginx -t 失败，请修正"

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
    else
        warn "未安装 curl，跳过部署后的功能探针"
    fi
""",
))

# 5) 既有告警分支也入账
PATCHES.append((
    """        if printf '%s' "$_body" | grep -qi 'Welcome to nginx'; then
            warn "80 端口仍返回 nginx 默认欢迎页：默认站点未被完全禁用。请检查 /etc/nginx/nginx.conf 是否内联了默认 server 块，或仍有其它 sites-enabled/* 配置冲突"
        elif ! systemctl is-active --quiet gipfel; then
            warn "nginx 已正确接管 80 端口，但后端 gipfel 服务未运行，访问将出现 502；请执行：sudo systemctl restart gipfel"
        else""",
    """        if printf '%s' "$_body" | grep -qi 'Welcome to nginx'; then
            problem "80 端口仍返回 nginx 默认欢迎页：默认站点未被完全禁用。请检查 /etc/nginx/nginx.conf 是否内联了默认 server 块，或仍有其它 sites-enabled/* 配置冲突"
        elif ! systemctl is-active --quiet gipfel; then
            problem "nginx 已正确接管 80 端口，但后端 gipfel 服务未运行，访问将出现 502；请执行：sudo systemctl restart gipfel"
        else""",
))

# 6) 收尾
PATCHES.append((
    """# ---------------- 收尾 ----------------
echo
ok "部署完成！"
echo""",
    """# ---------------- 收尾 ----------------
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
echo""",
))


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    changed = 0
    for old, new in PATCHES:
        o, n = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        if o not in text:
            if n in text:
                print("[skip] 已打过补丁")
                continue
            print(f"[fail] 未找到锚点: {old.splitlines()[0][:70]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    TARGET.write_bytes(text.encode("utf-8"))
    b = TARGET.read_bytes()
    print(f"[ok]   替换 {changed}/{len(PATCHES)} 处；CRLF={b.count(bytes([13, 10]))}")
    return 0 if changed == len(PATCHES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
