"""X-22：deploy-linux.sh 不再默认把管理员口令与密钥名打到 stdout/[DIAG]（CRLF 保持）。

新增 --print-seed-password：仅在**显式要求且 stdout 是 TTY** 时才显示明文口令。
"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / "scripts" / "deploy-linux.sh"

PATCHES: list[tuple[str, str]] = []

# 1) 新变量
PATCHES.append((
    """PUBLIC_IP_SET=0  # 标记 --public-ip 是否由用户显式传入（用于结尾提示区分「用户指定」与「自动探测」）""",
    """PUBLIC_IP_SET=0  # 标记 --public-ip 是否由用户显式传入（用于结尾提示区分「用户指定」与「自动探测」）
# 审计 X-22：改前脚本把 SEED_ADMIN_PASSWORD **明文**写进 stdout（生成处 + 结尾摘要），
# 部署若被 `| tee deploy.log`、CI 捕获、screen/tmux 回滚缓冲或堡垒机命令记录留存，
# 拿到日志的人就能在管理员首次登录前直接以 admin 接管系统。默认不再打印。
PRINT_SEED_PASSWORD=0""",
))

# 2) usage
PATCHES.append((
    """  --force-overwrite            即使 INSTALL_DIR 存在也覆盖（保留 backup）
  -h, --help                   显示本帮助""",
    """  --force-overwrite            即使 INSTALL_DIR 存在也覆盖（保留 backup）
  --print-seed-password        在**交互终端**（stdout 为 TTY）上显示初始管理员口令；
                               默认不显示，请自行 `sudo grep ^SEED_ADMIN_PASSWORD= <安装目录>/backend/.env`
  -h, --help                   显示本帮助""",
))

# 3) 参数解析
PATCHES.append((
    """        --force-overwrite)    FORCE_OVERWRITE=1; shift ;;""",
    """        --force-overwrite)    FORCE_OVERWRITE=1; shift ;;
        --print-seed-password) PRINT_SEED_PASSWORD=1; shift ;;""",
))

# 4) 生成处不再打印明文
PATCHES.append((
    """    echo "[DIAG] LOGVIEWER_SECRET_KEY 已生成并写入（$(date +%T)）" >&2""",
    """    # 审计 X-22：只报告"做了什么"，不枚举密钥名
    echo "[DIAG] 日志查看器防直连密钥已生成并写入（$(date +%T)）" >&2""",
))

PATCHES.append((
    """    ok "管理员密码已生成：admin / ${ADMIN_PW}（首次登录强制改密）"
    echo "[DIAG] SEED_ADMIN_PASSWORD 已生成并写入（$(date +%T)）" >&2""",
    """    if [[ "$PRINT_SEED_PASSWORD" == "1" && -t 1 ]]; then
        ok "管理员密码已生成：admin / ${ADMIN_PW}（首次登录强制改密）"
    else
        ok "管理员初始密码已生成并写入 ${INSTALL_DIR}/backend/.env 的 SEED_ADMIN_PASSWORD（首次登录强制改密）"
        ok "需要查看：sudo grep '^SEED_ADMIN_PASSWORD=' ${INSTALL_DIR}/backend/.env"
        if [[ "$PRINT_SEED_PASSWORD" == "1" ]]; then
            warn "--print-seed-password 已给出，但 stdout 不是终端（管道/CI）—— 为避免口令进入日志，此处不打印。"
        fi
    fi
    echo "[DIAG] 管理员初始口令已生成并写入（$(date +%T)）" >&2""",
))

PATCHES.append((
    """    echo "[DIAG] 首次部署 .env 生成完毕（$(date +%T)），JWT_SECRET/DJANGO_SECRET_KEY/LOGVIEWER_SECRET_KEY/SEED_ADMIN_PASSWORD 已就绪" >&2""",
    """    # 审计 X-22：改前这里把 4 个密钥名逐个枚举到 stderr —— 单行不敏感提示即可
    echo "[DIAG] 首次部署所需密钥/口令已全部生成并写入（$(date +%T)）" >&2""",
))

# 5) 结尾摘要
PATCHES.append((
    """# 读取管理员密码并醒目输出
_SEED_PW="$(grep -E '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' | sed -E "s/^['\\"]//; s/['\\"]$//")"
if [[ -n "$_SEED_PW" ]]; then
    echo "  👤 管理员账号：admin"
    echo "  🔑 管理员密码：${_SEED_PW}"
else
    echo "  👤 管理员账号：admin"
    echo "  🔑 管理员密码：admin23（默认值，建议登录后立即修改）"
fi""",
    """# 审计 X-22：改前这里无条件把 .env 里的 SEED_ADMIN_PASSWORD 明文回显到 stdout。
# 现在只有「显式 --print-seed-password 且 stdout 是终端」才显示，其余情况给查看命令。
echo "  👤 管理员账号：admin"
if [[ "$PRINT_SEED_PASSWORD" == "1" && -t 1 ]]; then
    _SEED_PW="$(grep -E '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' | sed -E "s/^['\\"]//; s/['\\"]$//")"
    if [[ -n "$_SEED_PW" ]]; then
        echo "  🔑 管理员密码：${_SEED_PW}"
    else
        echo "  🔑 管理员密码：admin23（默认值，建议登录后立即修改）"
    fi
else
    echo "  🔑 管理员密码：见 ${INSTALL_DIR}/backend/.env 的 SEED_ADMIN_PASSWORD"
    echo "     查看命令：sudo grep '^SEED_ADMIN_PASSWORD=' ${INSTALL_DIR}/backend/.env"
    echo "     （如需在本终端直接显示，可加 --print-seed-password 重跑）"
fi""",
))


def main() -> int:
    raw = TARGET.read_bytes()
    text = raw.decode("utf-8")
    changed = 0
    for old, new in PATCHES:
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
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
    print(f"[ok]   替换 {changed}/{len(PATCHES)} 处；CRLF={b.count(bytes([13, 10]))} 裸LF={b.count(bytes([10])) - b.count(bytes([13, 10]))}")
    return 0 if changed == len(PATCHES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
