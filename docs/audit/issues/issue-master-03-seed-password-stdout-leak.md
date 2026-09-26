> 以下整段可直接粘贴为 GitHub Issue（标题为第一行 `title:` 后的内容）。测试分支：`master`（提交 `34013bb`）。

---

**title:** `[deploy] 管理员初始口令被无条件写入 stdout，管道/CI 日志会留存管理员明文`

## 环境

| 项 | 值 |
| --- | --- |
| 分支 / commit | `master` / `34013bb` |
| 受影响文件 | `scripts/deploy-linux.sh:348`、`scripts/deploy-linux.sh:862-864`（另见 `:349` 的 `[DIAG]` 行） |
| 触发方式 | `sudo bash scripts/deploy-linux.sh … \| tee deploy.log`、CI 捕获 stdout、screen/tmux 回滚缓冲、堡垒机命令记录 |

## 问题描述

部署脚本把自动生成的管理员初始口令**无条件**打到 stdout，**不区分是否交互终端**：

```bash
# scripts/deploy-linux.sh:342-349（master，首次部署生成口令处）
ADMIN_PW="$(head -c 16 /dev/urandom | base64 | tr -d '\n+/=' | head -c 20)"
if grep -q '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env"; then
    sed -i -E "s|^SEED_ADMIN_PASSWORD=.*|SEED_ADMIN_PASSWORD=${ADMIN_PW}|" "$INSTALL_DIR/backend/.env"
else
    echo "SEED_ADMIN_PASSWORD=${ADMIN_PW}" >> "$INSTALL_DIR/backend/.env"
fi
ok "管理员密码已生成：admin / ${ADMIN_PW}（首次登录强制改密）"     # ← :348 无条件回显
echo "[DIAG] SEED_ADMIN_PASSWORD 已生成并写入（$(date +%T)）" >&2    # ← :349 单行提示（此前曾枚举全部密钥名）
```

```bash
# scripts/deploy-linux.sh:861-868（master，收尾摘要）
_SEED_PW="$(grep -E '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env" … || true)"
if [[ -n "$_SEED_PW" ]]; then
    echo "  👤 管理员账号：admin"
    echo "  🔑 管理员密码：${_SEED_PW}"          # ← :864 无条件回显
else
    echo "  👤 管理员账号：admin"
    echo "  🔑 管理员密码：admin23（默认值，建议登录后立即修改）"
fi
```

## 复现步骤

```bash
sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx | tee /tmp/deploy.log
grep -n '管理员密码' /tmp/deploy.log
# 输出示例：  🔑 管理员密码：Ab3xY9kQ2mN7pR5tZ1wC        ← 明文进了日志
```

## 实际结果

- 口令明文出现在 stdout（生成处 + 结尾摘要两处），**与 stdout 是否为终端无关**；
- 只要部署输出被 `tee`、CI 捕获、screen/tmux 回滚缓冲或堡垒机命令记录留存，**在管理员首次登录前拿到该日志的人即可直接用 `admin` 接管系统**（首次登录会强制改密，但改密本身也需要原口令）。

## 期望结果

- stdout 是**交互终端**时显示口令（运维可用性）；
- stdout 不是终端（管道 / CI / 重定向）时**绝不打印**，改为给出去处与查看命令，例如：
  `🔑 管理员密码：见 <安装目录>/backend/.env 的 SEED_ADMIN_PASSWORD`
  `   查看命令：sudo grep '^SEED_ADMIN_PASSWORD=' <安装目录>/backend/.env`

## 建议修复（供参考，未提交）

```bash
if [[ -t 1 ]]; then
    ok "管理员密码已生成：admin / ${ADMIN_PW}（首次登录强制改密；仅在本终端显示，不写入日志）"
else
    ok "管理员初始密码已生成并写入 ${INSTALL_DIR}/backend/.env 的 SEED_ADMIN_PASSWORD（首次登录强制改密）"
    ok "需要查看：sudo grep '^SEED_ADMIN_PASSWORD=' ${INSTALL_DIR}/backend/.env"
fi
```

收尾摘要处用同一条件；并把 `[DIAG]` 行改成不枚举密钥名的单行提示。

## 备注

本仓库 `bugfix-merged` 分支的 X-22 审计条目已修过这一点（口径为"**显式 `--print-seed-password` 且 stdout 是终端**才打印"，非终端绝不打印）。若 master 采纳，建议同时保留"终端下默认可见"的可用性——即守卫条件用 `[[ -t 1 ]]`，而非"必须再传一个开关"。
