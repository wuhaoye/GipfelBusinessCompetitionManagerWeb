/**
 * C3 前端独立复算（验收方自写断言，不复用实现方的用例结论）。
 *
 * 用真实前端模块（frontend/src 打包，见 entries/ops_c3_entry.mjs），只替换
 * axios adapter / setInterval / document.hidden 三个边界，然后**自己算**：
 *   ① 稳态心跳请求数：可见 20s vs 隐藏 60s，折算 100 客户端 req/s；
 *   ② 304 条件请求是否真的省掉响应体、是否被视为成功（不清登录态、不停心跳）；
 *   ③ 隐藏 <20s 切回是否真的不补打、≥20s 是否**恰好**补 1 次；
 *   ④ 重连对账的并发上限、指数退避与抖动是否真的成立（含分布抽样）；
 *   ⑤ 产业字段批量端点是否把 N 个请求压成 1 个、>50 是否分批、失败是否回落。
 *
 * 用法（仓库根目录）：node tests/ops_check/probe_frontend_heartbeat.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const bundlePs1 = path.join(repo, "tests", "fix_verify", "frontend", "bundle.ps1");
const entry = path.join(here, "entries", "ops_c3_entry.mjs");

function buildBundle() {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "gipfel-ops-c3-"));
  const r = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", bundlePs1, "-Entry", entry, "-Outfile", "ops_c3.mjs"],
    { cwd: outDir, stdio: "inherit" },
  );
  if (r.error) throw new Error(`打包失败：${r.error.message}`);
  return path.join(outDir, "ops_c3.mjs");
}

const bundlePath = process.argv[2] ? path.resolve(process.argv[2]) : buildBundle();
const mod = await import(pathToFileURL(bundlePath).href);
const {
  api, browserStub, COMPANY_FIELDS_BATCH_LIMIT, createPinia, getAccountItem,
  RECONCILE_CONCURRENCY, RECONCILE_MAX_ATTEMPTS, RECONCILE_RETRY_BASE_MS, RECONCILE_RETRY_MAX_MS,
  RECONCILE_START_JITTER_MS, reconcileCompanyFields, reconcileRetryDelayMs, reconcileStartDelayMs,
  runWithConcurrency, setAccountItem, setActivePinia, setActiveUser, useAuthStore,
} = mod;

let pass = 0;
let fail = 0;
const findings = [];
const lines = [];
async function check(name, fn) {
  try {
    await fn();
    lines.push(`[PASS] ${name}`);
    pass += 1;
  } catch (e) {
    lines.push(`[FAIL] ${name}: ${String(e && e.message).split("\n")[0]}`);
    fail += 1;
  } finally {
    globalThis.document.hidden = false;
  }
}

// ===================== 可控时钟 / 可见性 / 网络 =====================
const clock = { intervals: [] };
let seq = 1;
function installFakeClock() {
  const fakeSetInterval = (fn, ms) => {
    const id = (seq += 1);
    clock.intervals.push({ id, ms, fn });
    return id;
  };
  globalThis.setInterval = fakeSetInterval;
  globalThis.clearInterval = () => {};
  globalThis.window.setInterval = fakeSetInterval;
  globalThis.window.clearInterval = () => {};
}
installFakeClock();
const flush = (ms = 12) => new Promise((r) => setTimeout(r, ms));
const latestIntervalMs = () => clock.intervals[clock.intervals.length - 1].ms;
async function fireTimes(n) {
  const iv = clock.intervals[clock.intervals.length - 1];
  for (let i = 0; i < n; i += 1) {
    iv.fn();
    await flush();
  }
}
let skew = 0;
const realNow = Date.now;
Date.now = () => realNow() + skew;
function enterHidden() {
  globalThis.document.hidden = true;
  globalThis.window.dispatchEvent(new globalThis.CustomEvent("visibilitychange"));
}
function leaveHidden(hiddenMs) {
  skew = hiddenMs == null ? 0 : hiddenMs;
  globalThis.document.hidden = false;
  globalThis.window.dispatchEvent(new globalThis.CustomEvent("visibilitychange"));
  skew = 0;
}

const net = { me: [], hurt: 0, batchCalls: 0, singleCalls: 0, mode: "ok", etag: '"ops-etag-1"', batchOk: true };
const jsonResponse = (config, data, headers = {}, status = 200) => ({
  config, data, status, statusText: "OK", headers,
});
api.defaults.adapter = async (config) => {
  const url = String(config.url || "");
  const headers = {};
  const raw = config.headers || {};
  for (const [k, v] of Object.entries(typeof raw.toJSON === "function" ? raw.toJSON() : raw)) {
    headers[String(k).toLowerCase()] = v;
  }
  if (url.includes("/auth/login")) {
    return jsonResponse(config, {
      code: 0, message: "ok", data: {
        token: "tok-ops",
        user: { id: 7, username: "ops", role: "PLAYER", permissions: [], companyScopes: [], mustChangePassword: false },
      },
    });
  }
  if (url.includes("/auth/me")) {
    net.me.push({ params: config.params, headers });
    if (net.mode === "unauthorized") {
      const err = new Error("Request failed with status code 401");
      err.response = { status: 401, data: { message: "账号已在其他设备登录" }, headers: {}, config };
      throw err;
    }
    if (net.mode === "not-modified" && headers["if-none-match"] === net.etag) {
      return { config, data: "", status: 304, statusText: "Not Modified", headers: { etag: net.etag } };
    }
    return jsonResponse(config, {
      code: 0, message: "ok",
      data: { id: 7, tokenVersion: 1, isActive: true, mustChangePassword: false },
    }, { etag: net.etag });
  }
  if (url.includes("/company-fields")) {
    const p = config.params || {};
    if (p.companyIds != null) {
      net.batchCalls += 1;
      if (!net.batchOk) {
        const err = new Error("Request failed with status code 404");
        err.response = { status: 404, data: { message: "not found" }, headers: {}, config };
        throw err;
      }
      const companies = {};
      for (const id of String(p.companyIds).split(",")) {
        companies[id] = { industryTypeId: 1, fields: [{ industryFieldId: Number(id) * 10, value: `v${id}` }], existingIds: [Number(id) * 10], incremental: true, serverTime: "2026-09-26T00:00:00Z" };
      }
      return jsonResponse(config, { code: 0, message: "ok", data: { companies, serverTime: "2026-09-26T00:00:00Z" } });
    }
    net.singleCalls += 1;
    return jsonResponse(config, {
      code: 0, message: "ok",
      data: { fields: [], existingIds: [], incremental: true, serverTime: "2026-09-26T00:00:00Z" },
    });
  }
  return jsonResponse(config, { code: 0, message: "ok", data: null });
};

// ===================== ① 心跳稳态请求数（独立复算）=====================
async function heartbeatSteadyState() {
  setActivePinia(createPinia());
  const store = useAuthStore();
  net.me.length = 0;
  clock.intervals.length = 0;
  await store.login("ops", "pwd");
  assert.equal(latestIntervalMs(), 20000, `可见态心跳间隔应为 20000ms，实际 ${latestIntervalMs()}`);
  // 可见 60s = 3 次
  await fireTimes(3);
  const visible60 = net.me.length;
  assert.equal(visible60, 3, `可见 60s 应打 3 次，实际 ${visible60}`);
  assert.ok(net.me.every((r) => String(r.params?.light) === "1"), "每次心跳都带 light=1");
  // 隐藏 180s = 3 次（若仍 20s 则应为 9 次）
  enterHidden();
  assert.equal(latestIntervalMs(), 60000, `隐藏态心跳间隔应为 60000ms，实际 ${latestIntervalMs()}`);
  await fireTimes(3);
  const hidden180 = net.me.length - visible60;
  assert.equal(hidden180, 3, `隐藏 180s 应打 3 次，实际 ${hidden180}`);
  const rVisible = 100 * (3 / 60);      // req/s
  const rHidden = 100 * (3 / 180);
  lines.push(
    `[INFO] 独立复算：100 客户端可见态 ${rVisible.toFixed(2)} req/s；全部隐藏时 ${rHidden.toFixed(2)} req/s；` +
    `降幅 ${((1 - rHidden / rVisible) * 100).toFixed(1)}%（心跳间隔 20s→60s）`,
  );
  findings.push(`F-heartbeat-rate visible=${rVisible.toFixed(2)}/s hidden=${rHidden.toFixed(2)}/s`);
  leaveHidden(0);
}

// ===================== ② 304 条件请求 =====================
async function conditional304() {
  setActivePinia(createPinia());
  const store = useAuthStore();
  net.me.length = 0;
  clock.intervals.length = 0;
  net.mode = "ok";
  await store.login("ops", "pwd");
  globalThis.window.__kicked = 0;
  globalThis.window.addEventListener("auth:kicked", () => { globalThis.window.__kicked += 1; });
  await fireTimes(1);
  assert.equal(net.me.length, 1, "首发 1 次");
  assert.equal(net.me[0].headers["if-none-match"], undefined, "首次心跳不带 If-None-Match");
  net.mode = "not-modified";
  await fireTimes(1);
  assert.equal(net.me.length, 2, "第二次心跳仍发出（304 也要打网络）");
  assert.equal(net.me[1].headers["if-none-match"], net.etag, "第二次带上一轮 ETag");
  assert.equal(store.isLoggedIn, true, "304 不得清登录态");
  assert.equal(globalThis.window.__kicked, 0, "304 不得派发 auth:kicked");
  const before = net.me.length;
  await fireTimes(1);
  assert.equal(net.me.length, before + 1, "304 之后心跳继续（未停）");
  net.mode = "ok";
}

// ===================== ③ 隐藏时长门槛 =====================
async function hiddenThreshold() {
  setActivePinia(createPinia());
  const store = useAuthStore();
  await store.login("ops", "pwd");
  // 隐藏 5s 后切回：不补打
  net.me.length = 0;
  enterHidden();
  leaveHidden(5000);
  await flush(30);
  assert.equal(net.me.length, 0, `隐藏 5s（<20s）切回不得补打，实际 ${net.me.length} 次`);
  // 隐藏 20s 后切回：恰好补 1 次
  net.me.length = 0;
  enterHidden();
  leaveHidden(20000);
  await flush(30);
  assert.equal(net.me.length, 1, `隐藏 20s（≥20s）切回应恰好补 1 次，实际 ${net.me.length} 次`);
  // 隐藏 90s 后切回：仍只补 1 次（不是按漏掉的次数补）
  net.me.length = 0;
  enterHidden();
  leaveHidden(90000);
  await flush(30);
  assert.equal(net.me.length, 1, `隐藏 90s 切回仍只补 1 次，实际 ${net.me.length} 次`);
}

// ===================== ④ mustChangePassword / 401 =====================
async function gateAnd401() {
  setActivePinia(createPinia());
  const store = useAuthStore();
  await store.login("ops", "pwd");
  store.user.mustChangePassword = true;
  clock.intervals.length = 0;
  store.startHeartbeat();
  assert.equal(clock.intervals.length, 0, "mustChangePassword 期间不得启动心跳");
  store.user.mustChangePassword = false;
  net.me.length = 0;
  store.startHeartbeat();
  net.mode = "unauthorized";
  globalThis.window.__kicked = 0;
  globalThis.window.addEventListener("auth:kicked", () => { globalThis.window.__kicked += 1; });
  await fireTimes(1);
  assert.equal(net.me.length, 1, "401 时确实发了心跳");
  const deadline = Date.now() + 800;
  while (Date.now() < deadline && globalThis.window.__kicked === 0) await flush(10);
  const diag =
    `kicked=${globalThis.window.__kicked} token=${JSON.stringify(getAccountItem("token"))} ` +
    `hash=${JSON.stringify(globalThis.window.location && globalThis.window.location.hash)} ` +
    `loggedIn=${store.isLoggedIn}`;
  assert.equal(globalThis.window.__kicked, 1, `401 必须由全局拦截器派发 auth:kicked（心跳不得本地吞掉）；${diag}`);
  assert.equal(getAccountItem("token"), null, `401 应清掉持久化 token；${diag}`);
  assert.equal(store.isLoggedIn, false, `401 后内存登录态应被清空；${diag}`);
  net.mode = "ok";
}

// ===================== ⑤ 对账并发上限 / 退避 / 抖动 =====================
async function concurrencyAndBackoff() {
  assert.equal(RECONCILE_CONCURRENCY, 2, `默认并发上限应为 2，实际 ${RECONCILE_CONCURRENCY}`);
  let inFlight = 0;
  let peak = 0;
  const done = [];
  const tasks = Array.from({ length: 10 }, (_v, i) => async () => {
    inFlight += 1;
    peak = Math.max(peak, inFlight);
    await new Promise((r) => setTimeout(r, 5));
    done.push(i);
    inFlight -= 1;
  });
  await runWithConcurrency(tasks);
  assert.equal(peak, RECONCILE_CONCURRENCY, `最大在途应等于 ${RECONCILE_CONCURRENCY}，实际 ${peak}`);
  assert.equal(done.length, 10, "所有任务都要执行（含失败的也不中断后续）");
  const errTasks = [async () => { throw new Error("boom"); }, async () => { done.push("after-err"); }];
  await runWithConcurrency(errTasks, 2);
  assert.ok(done.includes("after-err"), "单任务失败不得影响其余任务");

  // 退避：指数增长 + 抖动落在 [0.5,1.5)
  const low = [];
  const high = [];
  for (let a = 0; a < 6; a += 1) {
    const samples = Array.from({ length: 200 }, () => reconcileRetryDelayMs(a, Math.random));
    low.push(Math.min(...samples));
    high.push(Math.max(...samples));
    const expectedCap = Math.min(RECONCILE_RETRY_BASE_MS * 2 ** a, RECONCILE_RETRY_MAX_MS);
    assert.ok(Math.min(...samples) >= Math.floor(expectedCap * 0.5) - 1, `attempt=${a} 下界`);
    assert.ok(Math.max(...samples) <= Math.ceil(expectedCap * 1.5), `attempt=${a} 上界`);
  }
  assert.ok(low[1] > low[0], `退避应随 attempt 增长：${low.join(",")}`);
  assert.ok(high[5] <= RECONCILE_RETRY_MAX_MS * 1.5, "退避有封顶");
  const jitterSamples = Array.from({ length: 400 }, () => reconcileRetryDelayMs(0, Math.random) / RECONCILE_RETRY_BASE_MS);
  const jMin = Math.min(...jitterSamples);
  const jMax = Math.max(...jitterSamples);
  assert.ok(jMin >= 0.49 && jMax <= 1.51, `抖动范围应≈[0.5,1.5)，实际 [${jMin.toFixed(3)},${jMax.toFixed(3)}]`);
  assert.ok(jMax - jMin > 0.5, "抖动必须真的随机（避免全场同一毫秒重连）");
  const starts = Array.from({ length: 400 }, () => reconcileStartDelayMs(Math.random));
  assert.ok(Math.min(...starts) >= 0 && Math.max(...starts) <= RECONCILE_START_JITTER_MS,
    `启动抖动应在 [0,${RECONCILE_START_JITTER_MS}]，实际 [${Math.min(...starts)},${Math.max(...starts)}]`);
  // 注释/常量声明写的是 [0, RECONCILE_START_JITTER_MS)（左闭右开），但实现是
  // `Math.round(rand * 1000)`，rand 接近 1 时会**恰好取到 1000**（实测 400 次抽样出现过 1000）。
  // 影响 1ms，可忽略；但「右开区间」的文档表述与实际边界不一致，故记为发现。
  if (Math.max(...starts) === RECONCILE_START_JITTER_MS) {
    findings.push(
      `F-start-jitter-inclusive: reconcileStartDelayMs() 实测最大值为 ${RECONCILE_START_JITTER_MS}（含上界），` +
      `与常量注释「[0, ${RECONCILE_START_JITTER_MS}) 左闭右开」表述不符（Math.round 造成，影响 1ms）`,
    );
  }
  lines.push(
    `[INFO] 退避下界序列(ms)=${low.join(",")}；上界序列=${high.join(",")}；` +
    `抖动实测=[${jMin.toFixed(2)},${jMax.toFixed(2)}]×base；` +
    `MAX_ATTEMPTS=${RECONCILE_MAX_ATTEMPTS} BATCH_LIMIT=${COMPANY_FIELDS_BATCH_LIMIT}`,
  );
}

// ===================== ⑥ 批量端点 =====================
async function batchEndpoint() {
  const mk = (ids) => ids.map((id) => ({ collectionKey: `companyField|companyId=${id}`, companyId: id, baseline: "2026-09-26T00:00:00Z" }));
  net.batchCalls = 0;
  net.singleCalls = 0;
  net.batchOk = true;
  await reconcileCompanyFields(mk([1, 2, 3]));
  assert.equal(net.batchCalls, 1, `3 家公司应压成 1 个批量请求，实际 batch=${net.batchCalls} single=${net.singleCalls}`);
  assert.equal(net.singleCalls, 0, `批量成功时不得再打单点端点，实际 single=${net.singleCalls}`);
  // 51 家 = 1 批（50）+ 剩余 1 家：实现里「单公司分组直接走单点端点」（避免多一次旧后端 404 探测）
  net.batchCalls = 0;
  net.singleCalls = 0;
  await reconcileCompanyFields(mk(Array.from({ length: 51 }, (_v, i) => i + 1)));
  assert.equal(net.batchCalls, 1, `51 家应只发 1 个批量请求，实际 batch=${net.batchCalls} single=${net.singleCalls}`);
  assert.equal(net.singleCalls, 1, `剩余 1 家走单点端点（实现有意的回落），实际 single=${net.singleCalls}`);
  net.batchCalls = 0;
  net.singleCalls = 0;
  await reconcileCompanyFields(mk(Array.from({ length: 100 }, (_v, i) => i + 1)));
  assert.equal(net.batchCalls, 2, `100 家应分 2 批，实际 batch=${net.batchCalls}`);
  net.batchCalls = 0;
  net.singleCalls = 0;
  net.batchOk = false;
  await reconcileCompanyFields(mk([1, 2, 3]));
  assert.equal(net.singleCalls, 3, `批量失败应回落逐个端点（3 次），实际 ${net.singleCalls}`);
  net.batchOk = true;
}

// ===================== 执行：每条用例独立进程（避免 store 单例/事件监听泄漏串扰）=====
const CHECKS = {
  "H1-可见/隐藏间隔与稳态请求数": heartbeatSteadyState,
  "H2-304 条件请求语义": conditional304,
  "H3-隐藏<20s 不补打、≥20s 补 1 次": hiddenThreshold,
  "H4-mustChangePassword 不启动 + 401 交给拦截器": gateAnd401,
  "H5-并发上限/指数退避/抖动": concurrencyAndBackoff,
  "H6-批量端点分组/分批/回落": batchEndpoint,
};

function selfPath() {
  return fileURLToPath(import.meta.url);
}

async function runChild(name) {
  const fn = CHECKS[name];
  if (!fn) {
    console.log(`[FAIL] 未知用例 ${name}`);
    process.exit(1);
  }
  lines.length = 0;
  await check(name, fn);
  for (const l of lines) console.log(l);
  process.exit(fail ? 1 : 0);
}

async function runAll(bundle) {
  let ok = 0;
  let bad = 0;
  for (const name of Object.keys(CHECKS)) {
    const r = spawnSync(process.execPath, [selfPath(), bundle, `--only=${name}`], { stdio: "inherit" });
    // 子进程用退出码表达 PASS/FAIL；esbuild 打包产物路径随进程传递，保证测的是同一份代码
    if (r.status === 0) ok += 1;
    else bad += 1;
  }
  console.log(`[SUMMARY] pass=${ok} fail=${bad}${bad ? " FAILED" : ""}（每个用例独立进程，避免跨用例状态串扰）`);
  process.exit(bad ? 1 : 0);
}

const onlyArg = process.argv.find((a) => a.startsWith("--only="));
if (onlyArg) {
  await runChild(onlyArg.slice("--only=".length));
} else {
  const bundle = process.argv[2] && !process.argv[2].startsWith("--") ? path.resolve(process.argv[2]) : buildBundle();
  console.log(`[INFO] 打包产物：${bundle}`);
  await runAll(bundle);
}
