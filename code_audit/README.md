# code_audit · 审计取证工具目录

> 本目录是「代码缺陷审计 + 运维就绪度评估」那条线的**可复现工具与原始存档**。
> **结论性文档不在这里** —— 在 [`../docs/audit/`](../docs/audit/README.md)（总报告、366+ 条缺陷索引、21 个审计单元、master 运维对照、规则书转录）。
> **一次性过程产物也不在这里** —— 291 个 `_before`/`_after` 代码快照、43 个 `_*_msg.txt` 提交信息草稿、`_merge/` 合并过程、运行日志已移出到仓库外的 `../gipfel-archive-20260926/`（该目录内附逐项对照与找回方式）。

---

## 目录结构

| 子目录 | 内容 | 被回归用例调用？ |
| --- | --- | --- |
| `harness/` | 回归 harness：每条缺陷「改前 / 改后」的真实执行对照 | ✅ **10 个被 `tests/fix_verify/scripts/test_x*.py` 直接调用** |
| `harness/wsl/` | WSL(Ubuntu) 真 Linux 验证脚本（`_wsl_verify.sh` = 44 项检查） | ❌ 按 [`../docs/WSL与生产环境验证操作手册.md`](../docs/WSL与生产环境验证操作手册.md) 手工运行 |
| `patches/` | 当时的修复补丁脚本（`_x*_patch.py`、`_cw16_patch.py` 等） | ❌ 历史价值：补丁内容早已进主代码，这里保留「怎么改的」 |
| `probes/` | 独立只读复核探针（**不写业务库**） | ❌ 见 [`../docs/audit/分支代码缺陷审计报告.md`](../docs/audit/分支代码缺陷审计报告.md) 附录 B |
| `tools/` | 生成器与辅助脚本 | `_build_index.py` 可重跑再生缺陷总索引 |
| `archives/` | 被文档引用的原始输出存档（`_WSL_verify_out.txt`、`_wsl21_out.txt`） | ❌ 只读 |

---

## 怎么跑

所有 harness 都**从自身位置推导仓库根**，因此在任意 cwd 下都能运行：

```powershell
# 纯 Python harness（Windows 上可真实执行）
backend\.venv\Scripts\python.exe code_audit\harness\_x18_harness.py after

# 依赖 bash 的 harness（本机没有可用 bash 时请改用 WSL / Linux）
bash code_audit/harness/_x22_harness.sh

# 重新生成缺陷总索引（输出与已入库版本逐字节一致：21 单元 / 368 条）
backend\.venv\Scripts\python.exe code_audit\tools\_build_index.py
```

## ⚠️ 移动 / 改名之前先看这里

harness 的仓库根推导是「脚本所在目录向上两级」，**再挪深一层就会失效**：

| 语言 | 写法 |
| --- | --- |
| Python | `REPO = Path(__file__).resolve().parents[2]` |
| shell | `REPO="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"` |

同时，10 个 harness 被回归用例按**字面路径**调用，改名需同步改这些文件：

| 用例 | harness |
| --- | --- |
| `tests/fix_verify/scripts/test_x10_rollback_path.py` | `harness/_x10_harness.sh` |
| `tests/fix_verify/scripts/test_x16_no_pseudo_tests.py` | `harness/_x16_strip_check.py` |
| `tests/fix_verify/scripts/test_x18_bootstrap_guard.py` | `harness/_x18_harness.py` |
| `tests/fix_verify/scripts/test_x19_start_dev_exit.py` | `harness/_x19_harness.py` |
| `tests/fix_verify/scripts/test_x20_install_dir.py` | `harness/_x20_harness.py` |
| `tests/fix_verify/scripts/test_x21_migrate_dryrun.py` | `harness/_x21_harness.py` |
| `tests/fix_verify/scripts/test_x22_secret_logging.py` | `harness/_x22_harness.sh` |
| `tests/fix_verify/scripts/test_x24_deploy_exit_code.py` | `harness/_x24_harness.sh` |
| `tests/fix_verify/scripts/test_x25_ssh_options.py` | `harness/_x25_harness.sh` |
| `tests/fix_verify/scripts/test_x30_runtime_user.py`（文档段引用） | `harness/_repro_gipfel_user_group.sh`（需 root + Linux） |

## 运行表现

- **纯 Python harness**（`_x16_strip_check.py`、`_x18_harness.py`、`_x19_harness.py`）在 Windows 上**会真实执行**——它们的存在消除了审计报告点名的「覆盖面静默丢失」。
- **依赖 bash 的 harness** 找不到 `bash.exe` 时由用例跳过（静态断言仍覆盖），在 Linux/WSL 上真实执行。
- 运行期临时目录（`code_audit/_probe_tmp/`、`code_audit/.probe/`、仓库根的 `.tmp/`）已被 `.gitignore` 忽略，不会污染 `git status`。
