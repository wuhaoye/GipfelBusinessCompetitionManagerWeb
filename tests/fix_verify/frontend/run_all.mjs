/**
 * 前端 fix_verify 全套一键复现（给 verifier / 封板复现用）。
 *
 * 背景：本目录 12 个用例把「打包后的模块路径」当 `process.argv[2]`，必须先打包；
 * 入口文件都在 ./entries/ 下。本脚本按「测试 → 入口」映射逐个打包（同一入口只打一次）
 * 再执行，最后打印 PASS/FAIL 表，退出码 = 是否有失败。
 *
 * 用法（仓库根目录，任意 cwd 均可）：
 *   node tests/fix_verify/frontend/run_all.mjs
 *   node tests/fix_verify/frontend/run_all.mjs --out D:\some\writable\dir
 *
 * cwd 无关：所有路径由 import.meta.url 推导（bundle.ps1 / entries / 测试文件），
 * 执行测试时统一以仓库根目录为 cwd，输出目录用绝对路径。已在「仓库根目录 /
 * tests\fix_verify 子目录 / %TEMP%」三处实测结果一致。
 *
 * 输出目录：默认 `os.tmpdir()/gipfel-fe-bundles`（本沙箱实测可用：
 * `C:\Users\<user>\AppData\Local\Temp\dsh-*\gipfel-fe-bundles`）。
 * 之所以默认不写进仓库：避免留打包产物。--out 可指向任意可写目录。
 *
 * 两个已知坑（脚本已规避，手工复现时注意）：
 *   1) 打包必须用 stdio 继承（`stdio: "inherit"`）。node 的 child_process 默认管道 stdio
 *      在本沙箱下会 EPERM（bundle.ps1 顶部注释也写了这点）。
 *   2) bundle.ps1 的 -Outfile 是用 `Join-Path (Get-Location).Path` 拼的，**绝对路径会拼坏**
 *      （C:\a + C:\b → 非法路径）。所以本脚本先 cd 到输出目录、-Outfile 只给文件名，
 *      -Entry 给绝对路径；手工在仓库根目录跑时 -Outfile 用仓库相对路径。
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");

const argv = process.argv.slice(2);
const outFlag = argv.indexOf("--out");
const outDir = outFlag >= 0 && argv[outFlag + 1]
  ? path.resolve(argv[outFlag + 1])
  : path.join(os.tmpdir(), "gipfel-fe-bundles");
fs.mkdirSync(outDir, { recursive: true });

/** [测试文件, 入口文件, 打包产物名]；入口相同的用例复用同一份产物。 */
const BUNDLED = [
  ["test_f06_logout_clears_memo.mjs", "entries/f06_entry.mjs", "f06.mjs"],
  ["test_m01_widget_packages_reload.mjs", "entries/m01_entry.mjs", "m01.mjs"],
  ["test_list_paging.mjs", "entries/paging_entry.mjs", "localPaging.mjs"],
  ["test_f12_page_bounds.mjs", "entries/paging_entry.mjs", "localPaging.mjs"],
  ["test_response_envelope.mjs", "entries/envelope_entry.mjs", "envelope.mjs"],
  ["test_format_time.mjs", "entries/format_entry.mjs", "format.mjs"],
  ["test_delete_confirm_message.mjs", "entries/delete_confirm_entry.mjs", "deleteConfirmMessage.mjs"],
  ["test_m01_widget_layout_preserved.mjs", "entries/layout_storage_entry.mjs", "layoutStorage.mjs"],
  ["test_v09_list_race_guards.mjs", "entries/use_latest_request_entry.mjs", "useLatestRequest.mjs"],
  ["test_w08_warehouses_race.mjs", "entries/use_latest_request_entry.mjs", "useLatestRequest.mjs"],
  ["test_t01_est_amount.mjs", "entries/est_amount_entry.mjs", "estAmount.mjs"],
  ["test_w02_vehicle_path_types.mjs", "entries/vehicle_path_types_entry.mjs", "vehiclePathTypes.mjs"],
];

/** 自带桩 / 自打包，直接 `node <file>` 即可。 */
const DIRECT = [
  "test_x31_forced_password_change_flow.mjs",
  "test_contract_tech_nodes_scope.mjs",
  "test_t02_double_submit.mjs",
  "test_v05_entity_cache_scope.mjs",
  "test_w03_option_competition_scope.mjs",
  "test_c3_heartbeat_backoff.mjs",
];

function runShell(cmd, cmdArgs, opts) {
  const r = spawnSync(cmd, cmdArgs, opts);
  if (r.error && r.error.code === "ENOENT") return null; // 该 shell 不存在 → 交给调用方回退
  return r;
}

function bundle(entryRel, outName) {
  const outPath = path.join(outDir, outName);
  const cmdArgs = [
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    path.join(here, "bundle.ps1"),
    "-Entry",
    path.join(here, entryRel),
    "-Outfile",
    outName,
  ];
  // stdio: "inherit" 是硬要求（管道 stdio 在本沙箱 EPERM）
  let r = runShell("powershell.exe", cmdArgs, { cwd: outDir, stdio: "inherit" });
  if (!r || (r.error && r.status == null && !fs.existsSync(outPath))) {
    r = runShell("pwsh", cmdArgs, { cwd: outDir, stdio: "inherit" });
  }
  if (!r || r.status !== 0 || !fs.existsSync(outPath)) {
    throw new Error(`打包失败：${entryRel}（exit=${r ? r.status : "spawn-error"}）`);
  }
  return outPath;
}

function runNode(testFile, bundlePath) {
  const testArgs = [path.join(here, testFile)];
  if (bundlePath) testArgs.push(bundlePath);
  const r = spawnSync(process.execPath, testArgs, { cwd: repo, stdio: "inherit" });
  return r.status === 0;
}

const results = [];
const bundleCache = new Map();

console.log(`\n=== 前端 fix_verify 全套（打包输出目录：${outDir}）===\n`);
for (const [testFile, entryRel, outName] of BUNDLED) {
  console.log(`\n----- ${testFile}  [entry: ${entryRel}] -----`);
  let ok = false;
  let err = "";
  try {
    if (!bundleCache.has(outName)) bundleCache.set(outName, bundle(entryRel, outName));
    ok = runNode(testFile, bundleCache.get(outName));
  } catch (e) {
    err = String(e.message || e);
    console.log(`  [ERROR] ${err}`);
  }
  results.push({ test: testFile, mode: `bundle:${entryRel}`, ok, err });
}

for (const testFile of DIRECT) {
  console.log(`\n----- ${testFile}  [直接运行，无需打包] -----`);
  const ok = runNode(testFile, null);
  results.push({ test: testFile, mode: "direct", ok, err: "" });
}

const pad = (s, n) => String(s) + " ".repeat(Math.max(0, n - String(s).length));
console.log("\n================ 汇总 ================");
console.log(`${pad("结果", 6)} ${pad("方式", 10)} 用例`);
for (const r of results) {
  console.log(`${pad(r.ok ? "PASS" : "FAIL", 6)} ${pad(r.mode.startsWith("bundle") ? "bundle" : "direct", 10)} ${r.test}${r.err ? `  (${r.err})` : ""}`);
}
const failed = results.filter((r) => !r.ok);
console.log(`\n套件合计：PASS=${results.length - failed.length} FAIL=${failed.length}（共 ${results.length} 个用例文件）`);
console.log(`打包产物：${outDir}`);
process.exit(failed.length === 0 ? 0 : 1);
