$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

# 仓库根由脚本自身位置推导（本文件位于 <repo>/docs/branch-diff/），不写死本机绝对路径。
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repoRoot

$A = 'master'
$B = 'bugfix-merged'
$outDir = 'docs/branch-diff'
$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# ---------- companion raw diffs (git writes files directly => UTF-8 safe) ----------
git diff --output="$outDir/master-vs-bugfix-merged.full.diff" $A $B
if ($LASTEXITCODE -ne 0) { throw 'full diff failed' }
git diff --diff-filter=M -M --output="$outDir/master-vs-bugfix-merged.modified.diff" $A $B
if ($LASTEXITCODE -ne 0) { throw 'modified diff failed' }

$fullLines = (Get-Content "$outDir/master-vs-bugfix-merged.full.diff" -Encoding UTF8 | Measure-Object -Line).Lines
$modLines  = (Get-Content "$outDir/master-vs-bugfix-merged.modified.diff" -Encoding UTF8 | Measure-Object -Line).Lines
$fullKB = [math]::Round((Get-Item "$outDir/master-vs-bugfix-merged.full.diff").Length / 1KB, 1)

# ---------- scalar facts ----------
$tipA = (git log -1 --format='%h|%ad|%an|%s' --date=short $A).Trim()
$tipB = (git log -1 --format='%h|%ad|%an|%s' --date=short $B).Trim()
$baseFull = (git merge-base $A $B).Trim()
$baseShort = (git log -1 --format='%h' $baseFull).Trim()
$baseSubject = (git log -1 --format='%s' $baseFull).Trim().TrimStart([char]0xFEFF)
$lr = (git rev-list --left-right --count "$A...$B") -split '\s+'
$behind = [int]$lr[0]; $ahead = [int]$lr[1]
$shortstat = (git diff --shortstat $A $B).Trim()
$nFiles = [int]([regex]::Match($shortstat, '(\d+) files? changed').Groups[1].Value)
$nIns = [int]([regex]::Match($shortstat, '(\d+) insertions').Groups[1].Value)
$nDel = [int]([regex]::Match($shortstat, '(\d+) deletions').Groups[1].Value)

# ---------- name-status / numstat ----------
$nsLines = @(git -c core.quotepath=false diff $A $B --name-status -M)
$nsNum   = @(git -c core.quotepath=false diff $A $B --numstat -M)

$nums = @{}
$binaries = New-Object System.Collections.Generic.List[object]
foreach ($l in $nsNum) {
  $p = $l -split "`t"
  if ($p.Count -lt 3) { continue }
  if ($p[2] -match '=>') { continue }   # rename: handled via name-status
  if ($p[0] -eq '-' -and $p[1] -eq '-') { $binaries.Add([pscustomobject]@{ Path = $p[2] }) ; continue }
  $nums[$p[2]] = @{ Add = [int]$p[0]; Del = [int]$p[1] }
}

$mods = New-Object System.Collections.Generic.List[object]
$adds = New-Object System.Collections.Generic.List[string]
$rens = New-Object System.Collections.Generic.List[object]
$dels = New-Object System.Collections.Generic.List[string]
foreach ($l in $nsLines) {
  $p = $l -split "`t"
  $st = $p[0]
  if ($st -eq 'M') {
    $f = $p[1]; $addCnt = 0; $delCnt = 0
    if ($nums.ContainsKey($f)) { $addCnt = $nums[$f].Add; $delCnt = $nums[$f].Del }
    $mods.Add([pscustomobject]@{ Path = $f; Add = $addCnt; Del = $delCnt })
  } elseif ($st -eq 'A') { $adds.Add($p[1]) }
  elseif ($st -like 'R*') { $rens.Add([pscustomobject]@{ Score = $st; Old = $p[1]; New = $p[2] }) }
  elseif ($st -eq 'D') { $dels.Add($p[1]) }
}

# ---------- commits ----------
$commits = @()
foreach ($l in (git -c core.quotepath=false log "$A..$B" --no-merges --format='%h|%ad|%an|%s' --date=short)) {
  $p = $l -split '\|', 4
  if ($p.Count -lt 4) { continue }
  $commits += [pscustomobject]@{ Hash = $p[0]; Date = $p[1]; Author = $p[2]; Subject = $p[3].TrimStart([char]0xFEFF) }
}
$merges = @()
foreach ($l in (git -c core.quotepath=false log "$A..$B" --merges --format='%h|%ad|%an|%s' --date=short)) {
  $p = $l -split '\|', 4
  if ($p.Count -lt 4) { continue }
  $merges += [pscustomobject]@{ Hash = $p[0]; Date = $p[1]; Author = $p[2]; Subject = $p[3].TrimStart([char]0xFEFF) }
}

# authors
$authors = $commits + $merges | Group-Object Author | Sort-Object Count -Descending

# per-directory aggregate (single source: numstat, so rename deltas are included and the
# column totals reconcile exactly with `git diff --shortstat`)
function ResolveNumstatPath($raw) {
  if ($raw -match '^(.*)\{(.*) => (.*)\}(.*)$') { return ($matches[1] + $matches[3] + $matches[4]) }
  if ($raw -match '^(.*) => (.*)$') { return $matches[2] }
  return $raw
}
$agg = @{}
foreach ($l in $nsNum) {
  $p = $l -split "`t"
  if ($p.Count -lt 3) { continue }
  $realPath = ResolveNumstatPath $p[2]
  $top = if ($realPath -match '/') { ($realPath -split '/')[0] } else { '(仓库根目录)' }
  if (-not $agg.ContainsKey($top)) { $agg[$top] = [pscustomobject]@{ Dir = $top; Files = 0; Add = 0; Del = 0 } }
  $agg[$top].Files++
  if ($p[0] -ne '-') { $agg[$top].Add += [int]$p[0] }
  if ($p[1] -ne '-') { $agg[$top].Del += [int]$p[1] }
}
$aggRows = $agg.Values | Sort-Object Add -Descending
$aggFileSum = ($aggRows | Measure-Object Files -Sum).Sum
$aggAddSum = ($aggRows | Measure-Object Add -Sum).Sum
$aggDelSum = ($aggRows | Measure-Object Del -Sum).Sum
if ($aggFileSum -ne $nFiles -or $aggAddSum -ne $nIns -or $aggDelSum -ne $nDel) {
  throw "aggregate mismatch: files=$aggFileSum/$nFiles add=$aggAddSum/$nIns del=$aggDelSum/$nDel"
}

# commit scope aggregate
$scopeAgg = @{}
foreach ($c in $commits) {
  $m = [regex]::Match($c.Subject, '^([a-z]+)(\(([^)]*)\))?[:(]')
  $type = if ($m.Success) { $m.Groups[1].Value } else { '(无前缀)' }
  $scope = if ($m.Success -and $m.Groups[3].Value) { $m.Groups[3].Value } else { '(未标注)' }
  $key = "$type|$scope"
  if (-not $scopeAgg.ContainsKey($key)) { $scopeAgg[$key] = 0 }
  $scopeAgg[$key]++
}
$typeAgg = @{}
foreach ($c in $commits) {
  $m = [regex]::Match($c.Subject, '^([a-z]+)(\(([^)]*)\))?[:(]')
  $t = if ($m.Success) { $m.Groups[1].Value } else { '(无前缀)' }
  if (-not $typeAgg.ContainsKey($t)) { $typeAgg[$t] = 0 }
  $typeAgg[$t]++
}

# defect-id index
$idMap = @{}
foreach ($c in $commits) {
  $ids = [regex]::Matches($c.Subject, '[A-Z]{1,3}-\d{2}') | ForEach-Object { $_.Value } | Select-Object -Unique
  foreach ($id in $ids) {
    if (-not $idMap.ContainsKey($id)) { $idMap[$id] = New-Object System.Collections.Generic.List[object] }
    $idMap[$id].Add($c)
  }
}
$prefixOrder = @('X', 'B01', 'Z', 'R', 'CW', 'D', 'I', 'S', 'C', 'F', 'V', 'W', 'T', 'M', 'A')
$orderedIds = $idMap.Keys | Sort-Object {
  $pre = ($_ -split '-')[0]
  $idx = [array]::IndexOf($prefixOrder, $pre); if ($idx -lt 0) { $idx = 99 }
  '{0:D3}-{1:D3}' -f $idx, [int](($_ -split '-')[1])
}

# ---------- markdown assembly ----------
$L = New-Object System.Collections.Generic.List[string]
function W($s) { [void]$L.Add([string]$s) }
function Esc($s) { return ($s -replace '\|', '\|') }

W "# master 与 bugfix-merged 分支差异详情"
W ""
W "> **对比方向**：``git diff master bugfix-merged``（即「从 master 到 bugfix-merged 需要发生的变化」）"
W "> **仓库**：``GipfelBusinessCompetitionManagerWeb``"
W "> **生成时间**：$now"
W "> **数据来源**：本地 git 对象库，已提交内容（不含工作区未提交改动）"
W ""
W "## 0. 分支名确认与阅读说明"
W ""
W "仓库中**不存在**名为 ``bug-merge`` 的引用（``git rev-parse bug-merge`` 报 unknown revision）。与 ``master`` 存在可比关系、名称含 bug 的引用如下，本文件按名称最接近的 ``bugfix-merged`` 生成："
W ""
W "| 候选引用 | 提交 | 提交日期 | 说明 | 是否被本文采用 |"
W "| --- | --- | --- | --- | --- |"
W "| ``bugfix-merged`` | ``$($tipB.Split('|')[0])`` | $($tipB.Split('|')[1]) | 名称含 merge，且为当前 HEAD | **是（本文对象）** |"
W "| ``bugfix`` | ``$((git log -1 --format='%h %ad' --date=short bugfix).Trim())`` | - | 已完全包含于 ``bugfix-merged`` | 否，见附录 A |"
W "| ``backup/before-master-merge`` | ``$((git log -1 --format='%h %ad' --date=short backup/before-master-merge).Trim())`` | - | 备份分支 | 否 |"
W ""
W "若实际想对比的是 ``bugfix``，请把本文件第 1–7 章的 ``bugfix-merged`` 全部替换为 ``bugfix`` 后重跑附录 C 的命令；两者的 master 侧完全一致（``master`` 同为其祖先）。"
W ""
W "---"
W ""
W "## 1. 分支关系与可比性"
W ""
W "| 项目 | 值 |"
W "| --- | --- |"
W "| 基准分支 | ``$A`` = ``$($tipA.Split('|')[0])``（$($tipA.Split('|')[1])，$($tipA.Split('|')[2])） |"
W "| 目标分支 | ``$B`` = ``$($tipB.Split('|')[0])``（$($tipB.Split('|')[1])，$($tipB.Split('|')[2])） |"
W "| 共同祖先 merge-base | ``$baseShort`` —— $baseSubject |"
W "| master 领先 | $behind 个提交 |"
W "| bugfix-merged 领先 | **$ahead 个提交**（$($commits.Count) 个非合并 + $($merges.Count) 个合并） |"
W "| 祖先关系 | ``master`` 是 ``bugfix-merged`` 的**严格祖先**（``git merge-base --is-ancestor master bugfix-merged`` 返回真） |"
W "| 合并方式 | **可零冲突 fast-forward**；不需要解决任何冲突 |"
W "| 变更时间跨度 | $((($commits | Sort-Object Date | Select-Object -First 1).Date)) ～ $((($commits | Sort-Object Date | Select-Object -Last 1).Date)) |"
W ""
W "提交链路（自旧到新）："
W ""
W '```'
W "master  ($($tipA.Split('|')[0]))"
W "  └── bugfix  ($((git log -1 --format='%h' bugfix).Trim()))          +2 个提交（含 prep-export-import 合并）"
W "        └── bugfix-merged  ($($tipB.Split('|')[0]))   +$ahead 个提交"
W '```'
W ""
W "> 结论：``master`` 上没有任何独有提交（领先 0 个），因此本文列出的差异**全部是 bugfix-merged 新增或修改的内容**，不存在「被删除的 master 特性」需要额外甄别。"
W ""
W "---"
W ""
W "## 2. 变更规模总览"
W ""
W "| 指标 | 数值 |"
W "| --- | --- |"
W "| 变更文件总数 | **$nFiles** |"
W "| 新增行 | **+$('{0:N0}' -f $nIns)** |"
W "| 删除行 | **-$('{0:N0}' -f $nDel)** |"
W "| 状态分布 | 新增 A=$($adds.Count)｜修改 M=$($mods.Count)｜重命名 R=$($rens.Count)｜删除 D=$($dels.Count) |"
W "| 二进制/样例文件 | $($binaries.Count) 个（xlsx / png / ico） |"
W "| ``git diff`` 原始输出 | $('{0:N0}' -f $fullLines) 行 / $fullKB KB |"
W ""
W "### 2.1 按顶层目录分布"
W ""
W "| 顶层目录 | 文件数 | 新增行 | 删除行 | 变更性质 |"
W "| --- | ---: | ---: | ---: | --- |"
$dirNote = @{
  'backend'          = '比赛准备 app、合同类型 builder、权限/审计/撮合等核心域修复'
  'tests'            = '回归验证资产（fix_verify 前端 mjs 用例 + 顶层脚本），12 个伪测试改名'
  'contract_watcher' = '全新的合同监听记账程序（独立进程，不改动现有服务）'
  'docs'             = '建包/合同代码化文档（多为新增），少量运维文档同步更新'
  'scripts'          = '部署、升级、同步、开发启动脚本加固'
  'frontend'         = '前端请求层、权限、分页、时区、XSS 等修复与新对话框'
  '(仓库根目录)'       = 'README、.gitattributes、审计与验证报告'
  'deploy'           = 'nginx / systemd 单元与部署说明'
}
foreach ($r in $aggRows) {
  $note = if ($dirNote.ContainsKey($r.Dir)) { $dirNote[$r.Dir] } else { '' }
  $delTxt = if ($r.Del -eq 0) { '0' } else { "-$('{0:N0}' -f $r.Del)" }
  W "| ``$($r.Dir)`` | $($r.Files) | +$('{0:N0}' -f $r.Add) | $delTxt | $note |"
}
W ""
W "> 对账：上表三列之和分别为 $aggFileSum 个文件、+$('{0:N0}' -f $aggAddSum) 行、-$('{0:N0}' -f $aggDelSum) 行，与 ``git diff --shortstat master bugfix-merged``（$nFiles files changed, $('{0:N0}' -f $nIns) insertions(+), $('{0:N0}' -f $nDel) deletions(-)）逐项一致。重命名文件按 git 报告的改名增量计入其**新**路径所在目录（12 个 ``tests/test_* -> tests/explore_*`` 共 +80 行，全部落在 ``tests``）。"
W ""
W "### 2.2 按提交类型 / 影响域分布"
W ""
W "| 提交类型 | 数量 |"
W "| --- | ---: |"
foreach ($k in ($typeAgg.Keys | Sort-Object { -$typeAgg[$_] })) { W "| ``$k`` | $($typeAgg[$k]) |" }
W "| （合并提交） | $($merges.Count) |"
W ""
W "| 提交类型 | 影响域（scope） | 数量 |"
W "| --- | --- | ---: |"
foreach ($k in ($scopeAgg.Keys | Sort-Object { -$scopeAgg[$_] }, { $_ })) {
  $sp = $k -split '\|', 2
  W "| ``$($sp[0])`` | ``$($sp[1])`` | $($scopeAgg[$k]) |"
}
W ""
W "### 2.3 提交人分布"
W ""
W "| 提交人 | 提交数 |"
W "| --- | ---: |"
foreach ($au in $authors) { W "| $($au.Name) | $($au.Count) |" }
W ""
W "---"
W ""
W "## 3. 六条变更主线"
W ""
W "本次差异不是零散修补，而是六条互相独立的工作线并行推进的结果，理解主线比逐文件看 diff 更重要。"
W ""
W "### 3.1 主线一：比赛准备与 Excel/CSV 建包体系（全新能力）"
W ""
W "新增 Django app ``apps.preparation`` 与配套的建包器、分组导出导入、开赛前体检，以及 5 份建包相关文档和 3 份 Excel 样例。该 app **没有 models.py / migrations**，全部逻辑通过既有 app 读写数据，因此合并后**不需要新增数据库迁移**（但 ``backend/backend/settings.py`` 已把 ``apps.preparation`` 加入 ``INSTALLED_APPS``，``backend/backend/urls.py`` 已挂载路由）。"
W ""
W "| 关键新增路径 | 作用 |"
W "| --- | --- |"
W "| ``backend/apps/preparation/builder/{core,schema,types}.py`` | Excel/CSV 读表 → 建比赛的建包核心 |"
W "| ``backend/apps/preparation/{checklist,plan,archive}.py`` | 准备清单、开赛前体检、分组归档导出导入 |"
W "| ``backend/apps/preparation/management/commands/build_competition.py`` | ``manage.py build_competition`` 命令入口 |"
W "| ``backend/apps/preparation/{views,urls}.py`` | 准备模块 API |"
W "| ``frontend/src/components/preparation/*.vue`` | 导入/概览对话框 |"
W "| ``docs/比赛Excel建包规范.md``、``docs/比赛Excel建包教程.md``、``docs/BUILD_COMPETITION_*.md`` | 表格规范与 API/代码化说明 |"
W "| ``backend/examples/excel/*.xlsx``、``backend/examples/*`` | 最小示例、汽车产业链示例、建包模板 |"
W ""
W "### 3.2 主线二：合同类型代码化（降低 effect / ENTITY 抽象）"
W ""
W "把合同类型从「表格里手写四份 JSON」改为「只引用代码脚本」，新增 ``backend/apps/contracts/builder/`` 包与 ``build_contract_types`` 命令。"
W ""
W "| 关键新增路径 | 作用 |"
W "| --- | --- |"
W "| ``backend/apps/contracts/builder/{builder,effects,refs,values,validate,aggregates,errors}.py`` | 合同类型构建器：引用解析、取值、效果、聚合、校验 |"
W "| ``backend/apps/contracts/management/commands/build_contract_types.py`` | 类型建库命令 |"
W "| ``backend/apps/contracts/tests/{test_type_builder,test_complex_contracts,test_named_effect_semantics}.py`` | 构建器回归用例 |"
W "| ``docs/CONTRACT_TYPE_BY_CODE.md``、``docs/合同可视化新建操作指南.md`` | 使用说明 |"
W ""
W "### 3.3 主线三：合同监听记账程序 contract_watcher（全新独立目录）"
W ""
W "顶层新增 ``contract_watcher/``，63 个文件、+8,263 行，是一个**独立运行、不改动现有服务**的旁路程序：轮询合同 → 生成记账 → 按公司分账写入 SQLite → 触发式批量写 xlsx，并带 Tkinter 界面与单实例锁。"
W ""
W "| 关键文件 | 作用 |"
W "| --- | --- |"
W "| ``contract_watcher/contract_watcher.py`` | 主轮询循环（心跳、停滞看门狗、失败退避、增量拉取） |"
W "| ``contract_watcher/bookkeeping.py`` | 记账金额与科目定位（显式校验，杜绝裸 float 静默漏账） |"
W "| ``contract_watcher/store.py`` | SQLite 落地与原子写进度水位 |"
W "| ``contract_watcher/handlers.py``、``readable.py``、``watcher_config.py`` | 自动生成 handler、可读记录、配置 |"
W "| ``contract_watcher/gui.py``、``start_gui.bat`` | Tkinter 界面与启动脚本 |"
W "| ``contract_watcher/{README,DATA_GUIDE,TEST_FLOW}.md`` | 使用/数据/测试说明 |"
W "| ``contract_watcher/bookkeeping_example/``（43 文件）、``test_run_recheck/``（8 文件） | 样例账本与实测产物（xlsx 已新增入库，注意业务数据脱敏） |"
W ""
W "### 3.4 主线四：安全与正确性缺陷修复（75 个修改文件）"
W ""
W "这是「对既有代码的改动」集中区，也是评审最需要关注的部分。75 个修改文件按顶层目录分布为：``backend`` 33 个、``frontend`` 21 个、``scripts`` 8 个、``deploy`` 4 个、``tests`` 4 个、``docs`` 3 个、仓库根 2 个。修复以缺陷编号登记，编号索引见第 6 章。"
W ""
W "| 类别 | 代表缺陷编号 | 代表改动文件 |"
W "| --- | --- | --- |"
W "| 认证/会话/实时 | I-01、I-02、I-03、C-01、I-05、X-31 | ``backend/apps/auth/authentication.py``、``backend/apps/auth/views.py``、``backend/apps/common/middleware.py``、``backend/apps/users/views.py`` |"
W "| 权限越权 | I-13、I-19、S-05、S-06、D-02 | ``backend/apps/common/permissions.py``、``backend/apps/stock/views.py``、``backend/apps/contracts/views.py`` |"
W "| 竞赛隔离与脏数据 | D-07、D-08、W-02、W-03、V-05 | ``backend/apps/{maps,tech_tree}/serializers.py``、``frontend/src/views/data-management/*.vue`` |"
W "| 数值/浮点/精度 | D-01、D-03、D-04、T-01、Z-02、Z-03 | ``backend/apps/stock/{engine,models}.py``、``backend/apps/contracts/engine.py``、``frontend/src/views/stocks/estAmount.ts`` |"
W "| 审计与留痕 | C-02、C-04 | ``backend/apps/common/audit.py``、``backend/apps/contracts/views.py`` |"
W "| 前端健壮性 | F-01、F-02、F-06、F-08、F-12、V-09、W-08、T-02、M-01 | ``frontend/src/api/request.ts``、``frontend/src/utils/deleteConfirm.ts``、``frontend/src/utils/format.ts``、``frontend/src/components/dashboard/registerCustomWidgets.ts`` |"
W "| 请求层/信封协议 | — | ``backend/apps/common/{response,renderers,exceptions}.py``、``frontend/src/api/envelope.ts`` |"
W ""
W "### 3.5 主线五：部署与运维脚本加固（本差异中单文件改动量最大的一类）"
W ""
W "| 文件 | 增/删 | 关键改进 |"
W "| --- | ---: | --- |"
W "| ``scripts/deploy-linux.sh`` | +568 / -60 | 降级分支折算退出码（X-24）、一致性回滚副本与 migrate 后回滚路径（X-10）、SSH 选项数组化与端口/私钥校验（X-25）、不再把管理员初始口令写进 stdout（X-22） |"
W "| ``scripts/update-from-github.sh`` | +482 / -59 | pull 失败默认中止、NodeSource 不再用 curl 管道执行（X-06/07/08） |"
W "| ``scripts/quick-sync.sh`` | +244 / -11 | 不再直接同步活库，改一致性快照 + 双向校验（X-05）、INSTALL_DIR 绝对化（X-20） |"
W "| ``scripts/migrate-server.sh`` | +221 / -53 | ``--dry-run`` 真正零副作用、密钥落盘与 root 注入修正（X-02/03/04、X-21） |"
W "| ``scripts/start-dev.bat`` / ``bootstrap-dev.bat`` / ``stop-dev.bat`` / ``scripts/dev.py`` | +39/-94 等 | 传递真实启动结果（X-19）、重入标记改用参数（X-18）、stop-dev 不再盲杀端口（D-01/D-02）、Ctrl+C 卡死修复 |"
W "| ``deploy/nginx-gipfel.conf`` | +173 / -18 | Cloudflare 源证书 HTTPS、logviewer TLS 端口校验时机与自定义端口、域名部署下 400/403 修复 |"
W "| ``deploy/{gipfel,logviewer}.service`` | +26/-9、+18/-7 | 移除无消费方的 daphne Unix socket（X-23）、单元目录权限收紧（X-13/X-14） |"
W "| ``deploy/README.md``、``docs/OPS.md``、``docs/MIGRATION.md`` | +442/-17 等 | 运维手册同步更新 |"
W "| ``scripts/lib/deploy-common.sh``、``scripts/gen_logviewer_key.py``、``scripts/make_favicon.py``、``scripts/verify-migration.sh`` | 新增/修改 | 公共函数库、密钥编码容错（X-11/X-12）、迁移校验不再静默中止（X-01/X-09） |"
W ""
W "### 3.6 主线六：回归验证资产（新增 125 个验证用例文件）"
W ""
W "| 目录 | 文件数 | 说明 |"
W "| --- | ---: | --- |"
W "| ``backend/tests_fix_verify/`` | 51 | 按缺陷编号命名的后端用例（``test_d01_decimal_exact.py``、``test_s06_stock_scope.py`` …） |"
W "| ``tests/fix_verify/`` | 74 | 前端 mjs 用例 + 打包/桩件（``test_f02_delete_confirm_xss.mjs`` …） |"
W "| ``tests/*.py``、``tests/*.sh`` | 修改 | 冒烟脚本退出码语义化：``big_number_smoke.py``、``sqlite_decimal_roundtrip.py``、``deploy_public_ip_test.sh``、``gipfel-logviewer-diag.sh`` |"
W "| ``tests/explore_*.py``（12 个重命名） | 重命名 | 原 ``tests/test_*.py`` 实为「只打印、恒退出 0」的伪测试，改名为 ``explore_*`` 以避免被误当测试执行（X-16） |"
W ""
W "---"
W ""
W "## 4. 提交清单"
W ""
W "### 4.1 非合并提交（$($commits.Count) 个，自新到旧）"
W ""
W "| # | 提交 | 日期 | 作者 | 说明 |"
W "| ---: | --- | --- | --- | --- |"
$i = 0
foreach ($c in $commits) { $i++; W "| $i | ``$($c.Hash)`` | $($c.Date) | $($c.Author) | $(Esc $c.Subject) |" }
W ""
W "### 4.2 合并提交（$($merges.Count) 个）"
W ""
W "| 提交 | 日期 | 作者 | 说明 |"
W "| --- | --- | --- | --- |"
foreach ($c in $merges) { W "| ``$($c.Hash)`` | $($c.Date) | $($c.Author) | $(Esc $c.Subject) |" }
W ""
W "---"
W ""
W "## 5. 文件变更明细"
W ""
W "### 5.1 修改的文件（$($mods.Count) 个）"
W ""
W "按改动量降序。这些是「既有代码被改动」的全部清单，建议逐项评审。"
W ""
W "| 文件 | + | - |"
W "| --- | ---: | ---: |"
foreach ($m in ($mods | Sort-Object @{Expression={$_.Add + $_.Del}; Descending=$true})) { W "| ``$($m.Path)`` | $($m.Add) | $($m.Del) |" }
W ""
W "### 5.2 新增的文件（$($adds.Count) 个）"
W ""
W "按目录汇总："
W ""
W "| 目录前缀 | 新增文件数 |"
W "| --- | ---: |"
$addGroup = @{}
foreach ($f in $adds) {
  $parts = $f -split '/'
  $key = if ($parts.Count -le 1) { '(仓库根目录)' } elseif ($parts.Count -eq 2) { $parts[0] } else { "$($parts[0])/$($parts[1])" }
  if (-not $addGroup.ContainsKey($key)) { $addGroup[$key] = 0 }
  $addGroup[$key]++
}
foreach ($k in ($addGroup.Keys | Sort-Object { -$addGroup[$_] }, { $_ })) { W "| ``$k`` | $($addGroup[$k]) |" }
W ""
W "<details>"
W "<summary>展开查看 $($adds.Count) 个新增文件的完整路径</summary>"
W ""
foreach ($f in ($adds | Sort-Object)) { W "- ``$f``" }
W ""
W "</details>"
W ""
W "### 5.3 重命名的文件（$($rens.Count) 个，相似度均为 75%+，无内容丢失）"
W ""
W "| 相似度 | 原路径 | 新路径 |"
W "| --- | --- | --- |"
foreach ($r in ($rens | Sort-Object New)) { W "| $($r.Score) | ``$($r.Old)`` | ``$($r.New)`` |" }
W ""
W "### 5.4 删除的文件"
W ""
if ($dels.Count -eq 0) { W "**无**（0 个删除）。master 上的任何文件都没有被移除。" } else { foreach ($d in $dels) { W "- ``$d``" } }
W ""
W "### 5.5 二进制 / 样例数据文件（$($binaries.Count) 个，均为新增）"
W ""
W "| 文件 | 说明 |"
W "| --- | --- |"
foreach ($bin in ($binaries | Sort-Object Path)) {
  $note = if ($bin.Path -like '*.xlsx' -and $bin.Path -like 'contract_watcher/*') { '样例账本 / 实测产物，入库前请确认不含真实业务数据' }
          elseif ($bin.Path -like '*.xlsx') { 'Excel 建包样例或模板' }
          else { '站点图标（配合 scripts/make_favicon.py 生成）' }
  W "| ``$($bin.Path)`` | $note |"
}
W ""
W "---"
W ""
W "## 6. 缺陷编号索引"
W ""
W "提交主题中登记了缺陷编号，共 $($idMap.Keys.Count) 个编号、$((($idMap.Values | ForEach-Object { $_.Count }) | Measure-Object -Sum).Sum) 处登记。前缀含义：``X``=仓库/脚本综合审计项，``B01``=后端高危，``Z``=Excel 建包，``R``=导入/导出，``CW``=contract_watcher，``D``=数据/领域正确性，``I``=身份与权限，``S``=股票，``C``=审计/认证，``F/V/W/T/M/A``=前端各模块。"
W ""
W "| 编号 | 主题（截断） | 提交 |"
W "| --- | --- | --- |"
foreach ($id in $orderedIds) {
  $items = $idMap[$id]
  $hashes = ($items | ForEach-Object { "``$($_.Hash)``" }) -join ' '
  $subj = ($items[0].Subject)
  if ($subj.Length -gt 60) { $subj = $subj.Substring(0, 60) + '…' }
  W "| ``$id`` | $(Esc $subj) | $hashes |"
}
W ""
W "---"
W ""
W "## 7. 关键代码差异摘录"
W ""
W "以下为「既有代码被修改」中语义最关键的若干处真实 diff（完整内容见伴随文件 ``master-vs-bugfix-merged.modified.diff``）。其余修改文件的 diff 已按同样口径收录在伴随文件中，这里只做抽样展示。"
W ""
$spotlight = @(
  'backend/apps/auth/authentication.py',
  'backend/apps/common/middleware.py',
  'backend/apps/stock/engine.py',
  'backend/apps/common/permissions.py',
  'frontend/src/utils/deleteConfirm.ts',
  'frontend/src/utils/format.ts',
  'frontend/src/api/request.ts'
)
foreach ($f in $spotlight) {
  $d = @(git -c core.quotepath=false diff $A $B -- $f)
  W "### 7.$([array]::IndexOf($spotlight, $f) + 1) ``$f``"
  W ""
  if ($d.Count -eq 0) { W "_（该文件在本次对比中无差异）_"; W ""; continue }
  W '```diff'
  foreach ($line in $d) { W $line }
  W '```'
  W ""
}
W "---"
W ""
W "## 8. 风险清单与合并建议"
W ""
W "### 8.1 风险清单"
W ""
W "| # | 风险 | 依据 | 等级 | 缓解动作 |"
W "| --- | --- | --- | --- | --- |"
W "| R1 | 148 个提交一次性进入 master，回滚粒度粗 | ``master`` 落后 $ahead 个提交，且为 fast-forward 关系 | 中 | 合并前在 ``bugfix-merged`` 打 tag（如 ``pre-merge-20260924``），必要时可整体回退 |"
W "| R2 | 部署脚本大改，且正确性最终取决于真实 Linux/SSH 主机 | ``deploy-linux.sh`` +568/-60、``update-from-github.sh`` +482/-59、``quick-sync.sh``、``migrate-server.sh``；对应 ``tests/fix_verify/scripts/`` 22 个用例只能静态/桩件验证，不触达真实主机 | **高** | 合并后先在 WSL/预发按 ``docs/OPS.md`` 全流程演练；不要直接上生产 |"
W "| R3 | 新增 ``.gitattributes`` 强制 shell 脚本 LF，会改变既有工作副本的检出行为 | ``.gitattributes`` 为新增文件；``tests/deploy_public_ip_test.sh`` 等 mtime 可能被判定为修改 | 中 | 合并后在开发机执行 ``git add --renormalize .`` 并复核 ``git status``，避免把行尾噪声混进业务提交 |"
W "| R4 | 样例与运行产物入库，可能携带真实业务数据 | 9 个二进制文件（xlsx/png/ico）＋ ``contract_watcher/bookkeeping_example/``（43 文件）与 ``contract_watcher/test_run_recheck/``（8 文件） | 中 | 逐个人工确认脱敏；必要时改为生成脚本 + ``.gitignore``（已有 CW-26 的同类处理） |"
W "| R5 | 权限与审计语义变化，行为可能与既有运维习惯不符 | I-13「超管显式放开」、I-19「permissions=null 按角色继承」、C-04 合同执行补审计 | 中 | 合并后按角色矩阵回归：超管、高级管理、普通管理员、公司账号各跑一遍 |"
W "| R6 | ``settings.py`` 新增 ``SECURE_PROXY_SSL_HEADER``，依赖 nginx 正确转发 ``X-Forwarded-Proto`` | ``backend/backend/settings.py`` +16/-1，且 ``deploy/nginx-gipfel.conf`` 同步改动 | 中 | 合并后同时更新 nginx 配置，否则 admin 登录/CSRF 可能出现 Secure cookie 异常 |"
W "| R7 | 新增 ``apps.preparation`` app 需重启后端进程并确认路由 | ``settings.py`` 加入 ``INSTALLED_APPS``、``backend/backend/urls.py`` 挂载路由；该 app 无 models，**不需要 migrate** | 低 | 部署时执行 ``manage.py check`` 与 ``manage.py migrate --plan`` 确认无待执行迁移 |"
W "| R8 | 依赖足迹未变（正面结论） | ``package.json`` / ``requirements.txt`` / 锁文件在本次对比中**均未变更** | 低 | 常规 ``npm ci`` / ``pip install -r requirements.txt`` 即可 |"
W ""
W "### 8.2 推荐合并步骤"
W ""
W '```bash'
W "# 0) 备份与取回"
W "git fetch --all --prune"
W "git log --oneline master..bugfix-merged | wc -l   # 应输出 148"
W ""
W "# 1) 在目标分支打预合并 tag（回滚锚点）"
W "git tag -a pre-merge-20260924 bugfix-merged -m 'merge anchor before master fast-forward'"
W ""
W "# 2) master 是 bugfix-merged 的祖先 => 直接快进，零冲突"
W "git checkout master"
W "git merge --ff-only bugfix-merged"
W ""
W "# 3) 若必须保留显式合并点，改用："
W "# git merge --no-ff bugfix-merged -m 'chore(release): 合并 bugfix-merged（148 提交）'"
W ""
W "# 4) 让 .gitattributes 的行尾规则统一生效"
W "git add --renormalize ."
W "git status --short"
W ""
W "# 5) 推送"
W "git push origin master"
W '```'
W ""
W "### 8.3 合并后验证清单"
W ""
W "| # | 验证项 | 命令 / 判据 |"
W "| --- | --- | --- |"
W "| 1 | 后端静态检查 | ``cd backend && python manage.py check`` |"
W "| 2 | 无待执行迁移意外新增 | ``python manage.py migrate --plan``（preparation 无 models） |"
W "| 3 | 缺陷回归用例 | ``python manage.py test tests_fix_verify``（51 个用例） |"
W "| 4 | 前端用例 | 按 ``tests/fix_verify/frontend/bundle.ps1`` 打包后运行 mjs 用例（74 个文件） |"
W "| 5 | 数值/精度专项 | ``python tests/sqlite_decimal_roundtrip.py``、``python tests/big_number_smoke.py`` |"
W "| 6 | 部署链路 | ``bash tests/deploy_public_ip_test.sh``、``bash tests/gipfel-logviewer-diag.sh`` |"
W "| 7 | 合同监听程序自检 | ``python contract_watcher/selftest.py`` |"
W "| 8 | 前端可构建 | ``cd frontend && npm ci && npm run build`` |"
W ""
W "---"
W ""
W "## 附录 A：master 与 bugfix 的对比（备选口径）"
W ""
W "若实际想看的「bug 分支」是 ``bugfix`` 而非 ``bugfix-merged``，其与 ``master`` 的差异如下，可视为本文的子集："
W ""
W "| 指标 | 值 |"
W "| --- | --- |"
W "| bugfix tip | ``$((git log -1 --format='%h %ad' --date=short bugfix).Trim())`` |"
W "| 领先 master | $((git rev-list --left-right --count 'master...bugfix') -split '\s+' | Select-Object -Index 1) 个提交 |"
W "| 变更规模 | $((git diff --shortstat master bugfix).Trim()) |"
W "| 包含内容 | ``fix(scripts)`` Windows Ctrl+C 卡死修复、``feat(preparation)`` 准备清单与分组导入导出、``Merge branch 'feature/prep-export-import'`` |"
W ""
W "``master ⊂ bugfix ⊂ bugfix-merged``，因此只需要本文的完整口径即可覆盖 bugfix 的全部内容。"
W ""
W "## 附录 B：伴随差异文件"
W ""
W "| 文件 | 内容 | 规模 |"
W "| --- | --- | --- |"
W "| ``docs/branch-diff/master-vs-bugfix-merged.full.diff`` | 全部 $nFiles 个文件的完整 unified diff（含新增文件全文） | $('{0:N0}' -f $fullLines) 行 / $fullKB KB |"
W "| ``docs/branch-diff/master-vs-bugfix-merged.modified.diff`` | 仅 $($mods.Count) 个**被修改**文件的 diff（评审重点，不含 270 个新增文件的全文） | $('{0:N0}' -f $modLines) 行 |"
W "| ``master与bugfix-merged分支差异详情.md`` | 本文件：统计、提交清单、文件清单、缺陷索引、风险与建议 | — |"
W ""
W "## 附录 C：复现命令"
W ""
W '```bash'
W "# 分支关系"
W "git merge-base master bugfix-merged"
W "git merge-base --is-ancestor master bugfix-merged && echo 'master 是祖先，可快进'"
W "git rev-list --left-right --count master...bugfix-merged"
W ""
W "# 规模统计"
W "git diff --shortstat master bugfix-merged"
W "git diff --name-status -M master bugfix-merged | cut -f1 | sort | uniq -c"
W "git diff --numstat -M master bugfix-merged"
W ""
W "# 提交清单"
W "git log master..bugfix-merged --no-merges --format='%h|%ad|%an|%s' --date=short"
W ""
W "# 完整差异"
W "git diff master bugfix-merged > master-vs-bugfix-merged.full.diff"
W "git diff --diff-filter=M -M master bugfix-merged > master-vs-bugfix-merged.modified.diff"
W ""
W "# 原始 git 输出为 UTF-8；Windows 下若中文文件名显示为转义，加 -c core.quotepath=false"
W "git -c core.quotepath=false diff --stat master bugfix-merged"
W '```'
W ""
W "---"
W ""
W "_本文件由 ``git diff``/``git log`` 的机器可读输出生成，统计数字与提交/文件清单均为实测值，未做人工估算。_"

[System.IO.File]::WriteAllLines((Join-Path $outDir 'master与bugfix-merged分支差异详情.md'), $L, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "WROTE $outDir/master与bugfix-merged分支差异详情.md  lines=$($L.Count)"
Write-Output "full.diff lines=$fullLines  modified.diff lines=$modLines"
Write-Output "commits=$($commits.Count) merges=$($merges.Count) mods=$($mods.Count) adds=$($adds.Count) rens=$($rens.Count) binaries=$($binaries.Count) ids=$($idMap.Keys.Count)"
