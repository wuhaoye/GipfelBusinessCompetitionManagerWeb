"""X-26：README 的镜像建议改为按仓库生效并补信任/撤销说明，回滚章节补全；脚本里的同款建议同步修正。"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "deploy" / "README.md"
UPDATE = REPO / "scripts" / "update-from-github.sh"

README_PATCHES: list[tuple[str, str]] = []

README_PATCHES.append((
    """> - 或让本机后续所有 git 操作自动走镜像（之后普通 `git clone` / `git pull` 即可，update-from-github.sh 的 pull 同样受益）：
>   `git config --global url."https://ghproxy.net/https://github.com/".insteadOf "https://github.com/"`
> - 镜像前缀可用性随时间变化，可依次尝试：`https://ghproxy.net/`、`https://ghfast.top/`、`https://gh-proxy.com/`、`https://mirror.ghproxy.com/`、`https://gitclone.com/github.com/`（挑能连的）。""",
    """> - 或让**当前仓库**后续 git 操作走镜像（在 clone 目录里执行；之后普通 `git pull` 即可，
>   `update-from-github.sh` 的 pull 同样受益）：
>   ```bash
>   cd /opt/GipfelBusinessCompetitionManagerWeb     # 你的 clone 目录
>   git config url."https://ghproxy.net/https://github.com/".insteadOf "https://github.com/"
>   # 用毕撤销：git config --unset url."https://ghproxy.net/https://github.com/".insteadOf
>   ```
>   **不要用 `--global`**：那会把机器上**所有** `https://github.com/` 请求改写到第三方代理域名，
>   包括携带 `Authorization` 头/私有仓库凭据的请求，代理可以记录甚至篡改代码。若已加过全局配置，
>   用 `git config --global --unset url."https://ghproxy.net/https://github.com/".insteadOf` 撤销。
>   按仓库配置只影响这一个 clone，风险面小得多。
> - 镜像前缀可用性随时间变化，可依次尝试：`https://ghproxy.net/`、`https://ghfast.top/`、`https://gh-proxy.com/`、`https://mirror.ghproxy.com/`、`https://gitclone.com/github.com/`（挑能连的）。""",
))

README_PATCHES.append((
    """### 回滚

```bash
# 当前代码目录改名，把 _backup 里最近的完整复制回来，再 restart 即可
sudo -u gipfel cp /opt/gipfel/_backup/2025-08-31_1200/db.sqlite3 /opt/gipfel/backend/
sudo systemctl restart gipfel
```""",
    """### 回滚

> **先看清楚**：`/opt/gipfel/_backup/<时间戳>/` 只含**数据**（`db.sqlite3`、`uploads/`、`.env`），
> **不含代码**。只恢复数据库不恢复代码，会得到"新代码 + 旧库"或"旧库 + 新代码"的版本错配 ——
> 尤其是已经跑过 `migrate` 的库，旧代码不一定认它的表结构。所以回滚要**数据与代码一起**做。

```bash
set -e
BK=/opt/gipfel/_backup/2025-08-31_1200          # 换成实际要回滚到的时间戳目录（ls /opt/gipfel/_backup/）
cd /opt/GipfelBusinessCompetitionManagerWeb      # 你的 clone 目录

# 0) 先记录当前版本，并把"现在"再备份一份（回滚本身也可能出错）
sudo git -C /opt/GipfelBusinessCompetitionManagerWeb rev-parse HEAD | tee /tmp/gipfel_rollback_from.txt
sudo -u gipfel cp -a /opt/gipfel/backend/db.sqlite3 "/opt/gipfel/_backup/db.sqlite3.before-rollback-$(date +%F_%H%M%S)"

# 1) 停服务（避免迁移/替换过程中仍有写入）
sudo systemctl stop gipfel gipfel-logviewer

# 2) 代码回退到上一个可用 tag/commit（不知道退到哪就用 git log --oneline 挑）
sudo git -C /opt/GipfelBusinessCompetitionManagerWeb checkout <上一个 tag 或 commit>

# 3) 恢复数据
sudo -u gipfel cp "$BK/db.sqlite3" /opt/gipfel/backend/db.sqlite3
[ -d "$BK/uploads" ] && sudo -u gipfel cp -a "$BK/uploads/." /opt/gipfel/backend/uploads/
[ -f "$BK/.env" ]    && sudo -u gipfel cp "$BK/.env" /opt/gipfel/backend/.env
sudo chown gipfel:gipfel /opt/gipfel/backend/db.sqlite3 /opt/gipfel/backend/.env
sudo chmod 600 /opt/gipfel/backend/.env

# 4) 按回退后的代码重装依赖、重建前端、回退数据库结构
sudo -u gipfel /opt/gipfel/backend/.venv/bin/pip install -r /opt/gipfel/backend/requirements.txt
cd /opt/GipfelBusinessCompetitionManagerWeb/frontend && sudo npm ci && sudo npm run build
# 若本次升级引入过新迁移，需要退回到旧迁移点（<app> 与迁移名取自 git show <旧commit>:backend/apps/<app>/migrations/）：
#   sudo -u gipfel /opt/gipfel/backend/.venv/bin/python /opt/gipfel/backend/manage.py migrate <app> <上一个迁移名>

# 5) 起服务并确认
sudo systemctl start gipfel gipfel-logviewer
curl -fsS --max-time 5 http://127.0.0.1/api/health && echo " 后端 OK"
systemctl is-active gipfel gipfel-logviewer
```

> 更稳的做法：不要在服务器上手工挑文件回滚，而是 `git revert` 出问题的那次改动并**重新跑一遍**
> `scripts/update-from-github.sh`（数据仍由 `_backup` 兜底）。`_backup/*` 只作最后手段。""",
))

UPDATE_PATCHES: list[tuple[str, str]] = []

UPDATE_PATCHES.append((
    """    warn "  git config --global url.\\"https://ghproxy.net/https://github.com/\\".insteadOf \\"https://github.com/\\""
    warn "镜像可用性随时间变化，也可尝试 ghfast.top / gh-proxy.com / mirror.ghproxy.com 等前缀。\"""",
    """    # 审计 X-26：改前这里推荐 `git config --global` —— 它会把机器上**所有**
    # https://github.com/ 请求（含携带凭据的私有仓库请求）改写到第三方代理域名。
    # 现在只推荐在当前 clone 目录里按仓库配置，并给出撤销命令。
    warn "  配置镜像（在部署目录里执行，**只影响该仓库**；不要用 --global）："
    warn "    git -C $INSTALL_DIR config url.\\"https://ghproxy.net/https://github.com/\\".insteadOf \\"https://github.com/\\""
    warn "  撤销：git -C $INSTALL_DIR config --unset url.\\"https://ghproxy.net/https://github.com/\\".insteadOf"
    warn "  若以前加过全局配置，请用 git config --global --unset url.\\"https://ghproxy.net/https://github.com/\\".insteadOf 撤销。"
    warn "镜像可用性随时间变化，也可尝试 ghfast.top / gh-proxy.com / mirror.ghproxy.com 等前缀。\"""",
))


def _patch(path: Path, patches: list[tuple[str, str]]) -> int:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    changed = 0
    for old, new in patches:
        o, n = old.replace("\n", nl), new.replace("\n", nl)
        if o not in text:
            if n in text:
                print(f"[skip] {path.name}: 已打过补丁")
                continue
            print(f"[fail] {path.name}: 未找到锚点 {old.splitlines()[0][:60]!r}")
            return 1
        text = text.replace(o, n, 1)
        changed += 1
    path.write_bytes(text.encode("utf-8"))
    print(f"[ok]   {path.name}: 替换 {changed}/{len(patches)} 处")
    return 0


def main() -> int:
    if _patch(README, README_PATCHES):
        return 1
    return _patch(UPDATE, UPDATE_PATCHES)


if __name__ == "__main__":
    raise SystemExit(main())
