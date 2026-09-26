/**
 * W-08 验证：WarehousesManager 的并发 loadData 必须只让「最后一次请求」写回状态。
 *
 * 改前 loadData 直接把响应写进 data.value：它会被首屏 / 切比赛（useCompetitionReload）/
 * 实时事件（useResourceChanged）/ 保存后刷新并发触发，晚到的旧响应会覆盖新数据
 * （切到比赛 B 后又显示回比赛 A 的列表；旧请求的 finally 还会提前关掉 loading）。
 *
 * 本文件从 SFC 抽出真实 loadData（tsc 转译）执行，配合**真实**的 useLatestRequest
 * （esbuild 构建产物）：改前 2 个用例失败，改后全部通过。改前状态下的
 * nextRequest / isCurrent 桩不会被旧代码引用，因此同一套断言可以直接跑改前代码。
 *
 * 用法（该模块 `import { ref } from "vue"`，**必须 --bundle --platform=browser**，
 * 否则 Node 侧 import 会 ERR_MODULE_NOT_FOUND: Cannot find package 'vue'）：
 *   frontend/node_modules/@esbuild/win32-x64/esbuild.exe frontend/src/composables/useLatestRequest.ts --format=esm --bundle --platform=browser --outfile=<临时目录>/useLatestRequest.mjs
 *   node tests/fix_verify/frontend/test_w08_warehouses_race.mjs <临时目录>/useLatestRequest.mjs
 * 也可直接用仓库入口一键打包（推荐）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests/fix_verify/frontend/bundle.ps1 -Entry tests/fix_verify/frontend/entries/use_latest_request_entry.mjs -Outfile tests/fix_verify/frontend/.build/useLatestRequest.mjs
 *   node tests/fix_verify/frontend/test_w08_warehouses_race.mjs tests/fix_verify/frontend/.build/useLatestRequest.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const modulePath = process.argv[2];
if (!modulePath) {
  console.error("用法: node test_w08_warehouses_race.mjs <构建后的 useLatestRequest.mjs 路径>");
  process.exit(2);
}
const { useLatestRequest } = await import(pathToFileURL(path.resolve(modulePath)).href);

const here = path.dirname(fileURLToPath(import.meta.url));
const viewPath = path.resolve(
  here,
  "../../../frontend/src/views/data-management/WarehousesManager.vue",
);
const source = fs.readFileSync(viewPath, "utf8").replace(/\r\n/g, "\n");

const require = createRequire(import.meta.url);
const ts = require(
  path.resolve(here, "../../../frontend/node_modules/typescript/lib/typescript.js"),
);
const transpile = (code) =>
  ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2020 } }).outputText;

const m = source.match(/async function loadData\(([^)]*)\) \{([\s\S]*?)\n\}/);
assert.ok(m, "无法从 WarehousesManager.vue 抽取 loadData（源码结构变了？）");

/** 造环境：api.get 返回可控的挂起 promise，可指定先发/后发响应 */
function makeHarness() {
  const calls = [];
  const pending = [];
  const scope = {
    data: { value: [{ id: "旧数据" }] },
    loading: { value: false },
    compStore: { competitionId: 1 },
    api: {
      get: async (url, config) => {
        const call = { url, params: config?.params, index: calls.length };
        calls.push(call);
        return new Promise((resolve, reject) => {
          call.resolve = (value) => resolve(value);
          call.reject = (err) => reject(err);
          pending.push(call);
        });
      },
    },
    console,
  };
  const guard = useLatestRequest();
  scope.nextRequest = guard.next;
  scope.isCurrent = guard.isCurrent;

  const run = new Function(
    ...Object.keys(scope),
    `return ${transpile(`(async (${m[1]}) => {${m[2]}\n})`)}`,
  )(...Object.values(scope));

  return { run, calls, scope };
}

const warehouses = (cid, n = 1) =>
  Array.from({ length: n }, (_, i) => ({ id: `${cid}-${i + 1}`, name: `比赛${cid}仓库${i + 1}` }));

let pass = 0;
let fail = 0;
const cases = [];

async function check(name, fn) {
  try {
    await fn();
    cases.push(`  [PASS] ${name}`);
    pass += 1;
  } catch (e) {
    cases.push(`  [FAIL] ${name}: ${e.message.split("\n")[0]}`);
    fail += 1;
  }
}

await check("单次请求正常写回（不回归）", async () => {
  const h = makeHarness();
  const p = h.run();
  h.calls[0].resolve(warehouses(1, 2));
  await p;
  assert.deepEqual(h.scope.data.value.map((x) => x.id), ["1-1", "1-2"]);
  assert.equal(h.scope.loading.value, false);
});

await check("乱序返回：先发的旧响应后到，不得覆盖新数据（改前被覆盖）", async () => {
  const h = makeHarness();
  const first = h.run(); // 旧请求
  h.scope.compStore.competitionId = 2;
  const second = h.run(); // 新请求（切比赛后）
  h.calls[1].resolve(warehouses(2, 1));
  await second;
  h.calls[0].resolve(warehouses(1, 3)); // 旧响应姗姗来迟
  await first;

  assert.deepEqual(
    h.scope.data.value.map((x) => x.id),
    ["2-1"],
    `晚到的旧响应不得覆盖新数据，实际 ${JSON.stringify(h.scope.data.value.map((x) => x.id))}`,
  );
});

await check("过时请求不得提前关闭 loading（新请求仍在飞行中）", async () => {
  const h = makeHarness();
  const first = h.run();
  const second = h.run();
  h.calls[0].resolve(warehouses(1)); // 旧请求先返回
  await first;
  assert.equal(h.scope.loading.value, true, "旧请求返回后 loading 应保持 true（新请求未完成）");
  h.calls[1].resolve(warehouses(1));
  await second;
  assert.equal(h.scope.loading.value, false);
});

await check("切比赛：旧比赛的请求失败也不得清空新比赛数据", async () => {
  const h = makeHarness();
  const first = h.run();
  h.scope.compStore.competitionId = 3;
  const second = h.run();
  h.calls[1].resolve(warehouses(3, 2));
  await second;
  h.calls[0].reject(new Error("network"));
  await first;
  assert.deepEqual(h.scope.data.value.map((x) => x.id), ["3-1", "3-2"]);
});

await check("最后一次请求失败：清空列表（既有行为不回归）", async () => {
  const h = makeHarness();
  const p = h.run();
  h.calls[0].reject(new Error("boom"));
  await p;
  assert.deepEqual(h.scope.data.value, []);
  assert.equal(h.scope.loading.value, false);
});

await check("未选择比赛：清空列表且不发请求（既有行为不回归）", async () => {
  const h = makeHarness();
  h.scope.compStore.competitionId = null;
  await h.run();
  assert.equal(h.calls.length, 0);
  assert.deepEqual(h.scope.data.value, []);
  assert.equal(h.scope.loading.value, false);
});

await check("源码守卫：loadData 必须使用 useLatestRequest 的代次守卫", () => {
  assert.match(m[2], /const token = nextRequest\(\)/, "必须记录本次请求代次");
  assert.match(m[2], /isCurrent\(token\)/, "写回前必须校验代次");
  assert.match(source, /useLatestRequest/, "必须引入 useLatestRequest");
});

console.log("W-08 仓库列表并发请求守卫：");
for (const c of cases) console.log(c);
console.log(`  通过 ${pass} / 失败 ${fail}`);
process.exitCode = fail > 0 ? 1 : 0;
