# Gipfel 商赛系统 · 服务器迁移指南

将 Gipfel 服务从一台服务器完整迁移到另一台服务器的操作手册。部署步骤见 [`deploy/README.md`](../deploy/README.md)；运维命令见 [`OPS.md`](OPS.md)。

---

## 1. 迁移概述

### 1.1 迁移内容

| 类别 | 数据 | 位置 | 重要性 |
| --- | --- | --- | --- |
| 数据库 | SQLite 数据库文件 | `backend/db.sqlite3` | ⭐⭐⭐ 核心 |
| 用户上传 | 比赛材料、头像等 | `backend/uploads/` | ⭐⭐⭐ 核心 |
| 环境配置 | JWT 密钥、数据库路径等 | `backend/.env` | ⭐⭐⭐ 核心 |
| 应用日志 | 运行日志（可选） | `backend/logs/` | ⭐ 可选 |
| 静态资源 | Django collectstatic 产物 | `backend/staticfiles/` | ⭐⭐ 重要 |
| 前端构建 | Vue.js 生产构建 | `frontend-dist/` | ⭐⭐ 重要 |
| 部署配置 | systemd / nginx 配置 | `deploy/` | ⭐⭐ 重要 |

### 1.2 迁移方式

| 方式 | 适用场景 | 脚本 |
| --- | --- | --- |
| **完整迁移** | 首次迁移、跨环境迁移 | `scripts/migrate-server.sh` |
| **快速同步** | 日常数据同步、增量更新 | `scripts/quick-sync.sh` |
| **手动迁移** | 特殊需求、脚本不可用时 | 见第 4 节 |

---

## 2. 使用迁移脚本（推荐）

### 2.1 完整迁移脚本 `migrate-server.sh`

**功能**：一键完成数据备份、传输、恢复、服务配置

**前置条件**：
- 新旧服务器之间 SSH 可互通
- 新服务器已安装 `rsync`、`python3`、`nginx`
- 源服务器 `$INSTALL_DIR` 目录存在

**使用方法**：

#### 方式一：推送模式（在旧服务器执行）

```bash
# 基本用法
sudo bash scripts/migrate-server.sh \
  --mode push \
  --target root@新服务器IP \
  --install-dir /opt/gipfel

# 使用自定义 SSH 端口
sudo bash scripts/migrate-server.sh \
  --mode push \
  --target root@新服务器IP \
  --ssh-port 2222

# 使用 SSH 密钥
sudo bash scripts/migrate-server.sh \
  --mode push \
  --target root@新服务器IP \
  --ssh-key ~/.ssh/id_rsa

# 模拟运行（不实际执行）
sudo bash scripts/migrate-server.sh \
  --mode push \
  --target root@新服务器IP \
  --dry-run
```

#### 方式二：拉取模式（在新服务器执行）

```bash
# 基本用法
sudo bash scripts/migrate-server.sh \
  --mode pull \
  --source root@旧服务器IP \
  --install-dir /opt/gipfel

# 跳过服务配置（仅传输数据）
sudo bash scripts/migrate-server.sh \
  --mode pull \
  --source root@旧服务器IP \
  --skip-services
```

**参数说明**：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `--mode` | ✅ | `push`（推送）或 `pull`（拉取） |
| `--target` | 推送时 | 目标服务器 `user@host` |
| `--source` | 拉取时 | 源服务器 `user@host` |
| `--install-dir` | ❌ | 安装目录，默认 `/opt/gipfel` |
| `--backup-dir` | ❌ | 临时备份目录，默认 `/tmp/gipfel-migration-<时间戳>` |
| `--skip-services` | ❌ | 跳过服务配置，仅传输数据 |
| `--dry-run` | ❌ | 模拟运行，不实际执行 |
| `--ssh-port` | ❌ | SSH 端口，默认 `22` |
| `--ssh-key` | ❌ | SSH 私钥路径 |

### 2.2 快速同步脚本 `quick-sync.sh`

**功能**：仅同步数据文件（数据库、上传、配置、日志），不包含代码

**适用场景**：
- 日常数据备份
- 增量数据同步
- 临时数据迁移

**使用方法**：

```bash
# 推送数据到新服务器
bash scripts/quick-sync.sh push root@新服务器IP

# 从旧服务器拉取数据
bash scripts/quick-sync.sh pull root@旧服务器IP

# 指定安装目录
bash scripts/quick-sync.sh push root@新服务器IP /opt/gipfel
```

**同步内容**：
- `backend/db.sqlite3` — 数据库
- `backend/uploads/` — 用户上传
- `backend/.env` — 环境配置
- `backend/logs/` — 应用日志

---

## 3. 迁移后验证

### 3.1 使用验证脚本

```bash
bash scripts/verify-migration.sh /opt/gipfel
```

**检查项目**：
- ✓ 文件完整性（数据库、配置、上传文件）
- ✓ 系统服务状态（gipfel、nginx）
- ✓ API 健康检查
- ✓ 文件权限
- ✓ 配置文件

### 3.2 手动验证

```bash
# 1. 检查服务状态
systemctl status gipfel
systemctl status gipfel-logviewer
systemctl status nginx

# 2. 健康检查
curl -sS http://127.0.0.1:8000/api/health
# 期望: {"code":0,"message":"成功","data":{"status":"ok"}}

# 3. 检查数据库
sqlite3 /opt/gipfel/backend/db.sqlite3 "SELECT count(*) FROM users_user;"
# 应返回用户数量

# 4. 检查上传文件
ls -la /opt/gipfel/backend/uploads/
# 应显示上传的文件

# 5. 浏览器访问
# 打开 http://新服务器IP/ 应显示登录页面
```

---

## 4. 手动迁移步骤

当脚本不可用或需要更细粒度控制时，可按以下步骤手动迁移。

### 4.1 在旧服务器备份数据

```bash
# 停止服务
sudo systemctl stop gipfel gipfel-logviewer

# 创建备份目录
BACKUP_DIR="/tmp/gipfel-backup-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 备份核心数据
sudo cp -a /opt/gipfel/backend/db.sqlite3 "$BACKUP_DIR/"
sudo cp -a /opt/gipfel/backend/.env "$BACKUP_DIR/"
sudo cp -a /opt/gipfel/backend/uploads "$BACKUP_DIR/"
sudo cp -a /opt/gipfel/backend/logs "$BACKUP_DIR/"

# 备份前端构建（可选，代码重新构建即可）
sudo cp -a /opt/gipfel/frontend-dist "$BACKUP_DIR/"

# 打包备份
cd /tmp
tar czf gipfel-migration.tar.gz "$(basename $BACKUP_DIR)"

# 重启服务
sudo systemctl start gipfel gipfel-logviewer

echo "备份完成: /tmp/gipfel-migration.tar.gz"
```

### 4.2 传输备份到新服务器

```bash
# 方式一：scp 传输
scp /tmp/gipfel-migration.tar.gz root@新服务器IP:/tmp/

# 方式二：rsync 传输（支持断点续传）
rsync -avz --progress /tmp/gipfel-migration.tar.gz root@新服务器IP:/tmp/

# 方式三：通过中间存储（对象存储、U盘等）
# 上传到对象存储后在新服务器下载
```

### 4.3 在新服务器恢复数据

```bash
# 解压备份
cd /tmp
tar xzf gipfel-migration.tar.gz

# 创建安装目录
sudo mkdir -p /opt/gipfel/backend

# 恢复数据
BACKUP_DIR=$(ls -d gipfel-backup-* | head -1)
sudo cp -a /tmp/$BACKUP_DIR/db.sqlite3 /opt/gipfel/backend/
sudo cp -a /tmp/$BACKUP_DIR/.env /opt/gipfel/backend/
sudo cp -a /tmp/$BACKUP_DIR/uploads /opt/gipfel/backend/
sudo cp -a /tmp/$BACKUP_DIR/logs /opt/gipfel/backend/

# 恢复前端（如果有）
if [[ -d "/tmp/$BACKUP_DIR/frontend-dist" ]]; then
    sudo cp -a /tmp/$BACKUP_DIR/frontend-dist /opt/gipfel/
fi

# 设置权限（运行用户与同名组；组已存在时改用 -g gipfel）
# 注意：必须以 root 登录环境执行（sudo -i 或 su -）—— 用 `su`（不带 -）时 PATH 不含
# /usr/sbin，useradd 会 command not found，随后的 chown 会以 "invalid user" 报错。
if ! id gipfel >/dev/null 2>&1; then
    sudo useradd -r -s /usr/sbin/nologin -U gipfel
fi
sudo chown -R gipfel:gipfel /opt/gipfel/backend
sudo chmod 600 /opt/gipfel/backend/.env
```

### 4.4 部署代码到新服务器

```bash
# 克隆代码
git clone https://github.com/bingdexzx/GipfelBusinessCompetitionManagerWeb.git /opt/GipfelBusinessCompetitionManagerWeb
cd /opt/GipfelBusinessCompetitionManagerWeb

# 运行部署脚本（会自动备份现有数据）
sudo bash scripts/deploy-linux.sh --install-dir /opt/gipfel --with-nginx

# 或者手动部署
cd /opt/gipfel/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput

# 构建前端
cd /opt/gipfel/frontend
npm ci
npm run build
sudo cp -r dist /opt/gipfel/frontend-dist
```

### 4.5 配置 nginx 和 systemd

```bash
# 安装 systemd 服务
sudo cp /opt/gipfel/deploy/gipfel.service /etc/systemd/system/
sudo cp /opt/gipfel/deploy/logviewer.service /etc/systemd/system/
sudo systemctl daemon-reload

# 配置 nginx
sudo cp /opt/gipfel/deploy/nginx-gipfel.conf /etc/nginx/sites-available/gipfel.conf
sudo sed -i "s|__INSTALL_DIR__|/opt/gipfel|g" /etc/nginx/sites-available/gipfel.conf
sudo sed -i "s|__DOMAIN__|YOUR_DOMAIN|g" /etc/nginx/sites-available/gipfel.conf
sudo ln -sf /etc/nginx/sites-available/gipfel.conf /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

# 测试并重启 nginx
sudo nginx -t && sudo systemctl reload nginx

# 启动服务
sudo systemctl enable gipfel gipfel-logviewer
sudo systemctl start gipfel gipfel-logviewer
```

---

## 5. 迁移流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                         迁移流程                                │
└─────────────────────────────────────────────────────────────────┘

    旧服务器                                    新服务器
        │                                          │
        │  ┌─────────────────────────────────────┐ │
        │  │ 1. 停止服务                         │ │
        │  │    systemctl stop gipfel            │ │
        │  └─────────────────────────────────────┘ │
        │                                          │
        │  ┌─────────────────────────────────────┐ │
        │  │ 2. 备份数据                         │ │
        │  │    db.sqlite3 + uploads + .env      │ │
        │  └─────────────────────────────────────┘ │
        │                                          │
        │  ┌─────────────────────────────────────┐ │
        │  │ 3. 传输数据                         │ │
        │  │    rsync / scp / 对象存储           │ │
        │  └──────────────┬──────────────────────┘ │
        │                 │                        │
        │                 └───────────────────────→│
        │                                          │
        │                  ┌───────────────────────┴──────────────┐
        │                  │ 4. 新服务器部署                      │
        │                  │    - 克隆代码                        │
        │                  │    - 恢复数据                        │
        │                  │    - 安装依赖                        │
        │                  │    - 配置服务                        │
        │                  └───────────────────────┬──────────────┘
        │                                          │
        │                  ┌───────────────────────┴──────────────┐
        │                  │ 5. 验证                            │
        │                  │    - 健康检查                        │
        │                  │    - 浏览器访问                      │
        │                  │    - 数据完整性                      │
        │                  └────────────────────────────────────┘
        │                                          │
        │  ┌─────────────────────────────────────┐ │
        │  │ 6. 切换 DNS / 负载均衡              │ │
        │  └─────────────────────────────────────┘ │
        │                                          │
```

---

## 6. 常见问题

### Q1. rsync 传输中断怎么办？

rsync 支持断点续传，重新执行相同命令即可：

```bash
rsync -avz --progress --partial /tmp/gipfel-migration.tar.gz root@新服务器IP:/tmp/
```

### Q2. 新旧服务器 SSH 端口不同怎么办？

使用 `--ssh-port` 参数指定端口：

```bash
sudo bash scripts/migrate-server.sh \
  --mode push \
  --target root@新服务器IP \
  --ssh-port 2222
```

或手动指定 rsync 的 SSH 端口：

```bash
rsync -avz -e "ssh -p 2222" /path/to/data root@新服务器IP:/path/to/dest
```

### Q3. 数据库文件很大怎么办？

SQLite 数据库文件可以直接复制，无需 dump：

```bash
# 直接复制（推荐，最简单）
rsync -avz --progress /opt/gipfel/backend/db.sqlite3 root@新服务器IP:/opt/gipfel/backend/

# 或者压缩后传输
gzip -c /opt/gipfel/backend/db.sqlite3 | ssh root@新服务器IP "gunzip -c > /opt/gipfel/backend/db.sqlite3"
```

### Q4. 迁移后用户无法登录怎么办？

检查以下几点：

1. **JWT_SECRET 一致**：确保新服务器 `.env` 的 `JWT_SECRET` 与旧服务器相同
2. **数据库完整**：检查 `db.sqlite3` 是否完整传输
3. **ALLOWED_HOSTS**：确保新服务器 IP/域名已加入 `.env` 的 `DJANGO_ALLOWED_HOSTS`

```bash
# 检查 .env 配置
cat /opt/gipfel/backend/.env | grep -E "JWT_SECRET|DJANGO_ALLOWED_HOSTS"

# 测试登录
curl -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin23"}'
```

### Q5. 上传文件丢失怎么办？

检查 `uploads` 目录是否正确恢复：

```bash
# 检查目录权限
ls -la /opt/gipfel/backend/uploads/

# 检查文件数量
find /opt/gipfel/backend/uploads/ -type f | wc -l

# 修复权限
sudo chown -R gipfel:gipfel /opt/gipfel/backend/uploads/
sudo chmod -R 755 /opt/gipfel/backend/uploads/
```

### Q6. nginx 配置不生效怎么办？

```bash
# 测试配置语法
sudo nginx -t

# 检查软链接
ls -la /etc/nginx/sites-enabled/

# 重新加载配置
sudo systemctl reload nginx

# 检查端口监听
sudo ss -tlnp | grep nginx
```

### Q7. 如何实现零停机迁移？

使用蓝绿部署策略：

1. 在新服务器部署并验证服务正常
2. 修改 DNS 或负载均衡配置，将流量切换到新服务器
3. 观察新服务器运行稳定后，停止旧服务器

```bash
# 1. 新服务器部署完成，验证健康
curl http://新服务器IP/api/health

# 2. 切换 DNS（以 Cloudflare 为例）
# 在 DNS 控制台将 A 记录指向新服务器 IP

# 3. 等待 DNS 生效（TTL 过期）
dig +short your-domain.com

# 4. 确认流量已切换，停止旧服务器
# 在旧服务器上
sudo systemctl stop gipfel gipfel-logviewer nginx
```

### Q8. 如何回滚到旧服务器？

如果迁移后发现问题，可以快速回滚：

```bash
# 1. 切换 DNS 回旧服务器
# 在 DNS 控制台将 A 记录指回旧服务器 IP

# 2. 在旧服务器启动服务
sudo systemctl start gipfel gipfel-logviewer nginx

# 3. 验证旧服务器正常
curl http://旧服务器IP/api/health
```

---

## 7. 最佳实践

### 7.1 迁移前检查清单

- [ ] 确认新服务器系统版本和依赖已安装
- [ ] 确认新旧服务器之间网络连通（SSH、HTTP）
- [ ] 备份旧服务器数据（至少备份 `db.sqlite3` 和 `.env`）
- [ ] 记录旧服务器的配置信息（端口、域名、环境变量）
- [ ] 选择低峰期进行迁移，减少对用户的影响

### 7.2 迁移后检查清单

- [ ] 服务状态正常（`systemctl status gipfel`）
- [ ] API 健康检查通过（`/api/health` 返回 200）
- [ ] 用户可以正常登录
- [ ] 数据库数据完整
- [ ] 上传文件可以正常访问
- [ ] WebSocket 连接正常（实时功能）
- [ ] 日志查看器可以访问
- [ ] nginx 配置正确（HTTPS、域名）

### 7.3 定期备份建议

建议定期备份核心数据，便于迁移和灾难恢复：

```bash
# 创建定时备份脚本
cat > /opt/gipfel/scripts/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/gipfel/_backup/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -a /opt/gipfel/backend/db.sqlite3 "$BACKUP_DIR/"
cp -a /opt/gipfel/backend/.env "$BACKUP_DIR/"
cp -a /opt/gipfel/backend/uploads "$BACKUP_DIR/"
echo "备份完成: $BACKUP_DIR"
EOF

chmod +x /opt/gipfel/scripts/backup.sh

# 添加 crontab（每天凌晨 3 点备份）
echo "0 3 * * * /opt/gipfel/scripts/backup.sh" | sudo crontab -
```

---

## 8. 脚本参考

### 8.1 `migrate-server.sh` 完整参数

```
用法: migrate-server.sh [选项]

必选参数（二选一）：
  --mode push --target USER@HOST    推送模式：从本机推送到目标服务器
  --mode pull --source USER@HOST    拉取模式：从源服务器拉取到本机

可选参数：
  --install-dir DIR     安装目录（默认: /opt/gipfel）
  --backup-dir DIR      临时备份目录（默认: /tmp/gipfel-migration-<时间戳>）
  --skip-services       跳过服务配置（仅传输数据）
  --dry-run             模拟运行，不实际执行
  --ssh-port PORT       SSH 端口（默认: 22）
  --ssh-key PATH        SSH 私钥路径
  -h, --help            显示帮助
```

### 8.2 `quick-sync.sh` 参数

```
用法: quick-sync.sh <push|pull> <user@host> [install-dir]

参数：
  push|pull      推送或拉取
  user@host      远程服务器地址
  install-dir    安装目录（默认: /opt/gipfel）
```

### 8.3 `verify-migration.sh` 参数

```
用法: verify-migration.sh [install-dir]

参数：
  install-dir    安装目录（默认: /opt/gipfel）
```
