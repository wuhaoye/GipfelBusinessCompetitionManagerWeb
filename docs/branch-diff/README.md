# 分支差异（branch-diff）

> 本目录存放 `master` 与 `bugfix-merged` 两条长期分支的差异报告，以及**可重跑的生成脚本**。

---

## 文件

| 文件 | 说明 |
| --- | --- |
| [master与bugfix-merged分支差异详情.md](master与bugfix-merged分支差异详情.md) | **报告正文**：分支名确认、分支关系与可比性、规模统计、提交清单（含合并提交）、文件清单（修改/新增/重命名/二进制）、缺陷索引、风险与建议、附录命令 |
| [gen-report.ps1](gen-report.ps1) | **生成脚本**（PowerShell）。仓库根由脚本自身位置推导，可在任意机器上重跑 |

## 重新生成

```powershell
# 在仓库任意位置执行均可；脚本会自己切到仓库根
pwsh -File docs/branch-diff/gen-report.ps1
```

脚本会产出三样东西：

| 产物 | 是否入库 | 原因 |
| --- | --- | --- |
| `master与bugfix-merged分支差异详情.md` | ✅ 入库 | 结论性报告 |
| `master-vs-bugfix-merged.full.diff` | ❌ 不入库（`.gitignore`） | 全量差异，数 MB 的机器生成物 |
| `master-vs-bugfix-merged.modified.diff` | ❌ 不入库（`.gitignore`） | 仅修改类差异，数百 KB 的机器生成物 |

> 两份 `.diff` 是**中间产物**：每次跑脚本都会重新生成，内容随分支推进而变，入库只会让仓库膨胀。需要时本地重跑即可。

## 口径

- 对比方向为 `git diff master bugfix-merged`，即「从 `master` 到 `bugfix-merged` 需要发生的变化」。
- 数据来源为**本地 git 对象库的已提交内容**，不含工作区未提交改动。
- 报告中的统计数字、提交/文件清单均为 `git diff`/`git log` 机器可读输出的实测值，未做人工估算。
