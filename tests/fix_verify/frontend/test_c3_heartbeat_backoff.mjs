/**
 * C3 验证：自适应条件心跳 + 重连退避/并发上限 + 产业字段批量端点接入。
 *
 * 依据：docs/运维约束整改设计说明.md §4.1/§4.2/§4.3 与 架构性运维约束整改简报.md C3.3/C3.4。
 *
 * 本用例跑的是**真实模块**（由 frontend/src 打包，见 entries/c3_entry.mjs）：
 *   - @/stores/auth：真实 login / startHeartbeat / stopHeartbeat / logout / visibilitychange 处理；
 *   - @/api + @/api/request：真实拦截器（逐调用 conditional 304 支持）与对账调度器。
 * 只有 axios adapter、定时器（setInterval/clearInterval）与 document.hidden 被替换成可控桩。
 *
 * 断言覆盖：
 *   ① 心跳：隐藏间隔 > 可见间隔（60s > 20s）、重新可见立即补一次、light=1；
 *   ② 条件请求：首次不带 If-None-Match，200 存 ETag，之后带上；304 视为成功
 *      （不清登录态 / 不派发 auth:kicked / 不停心跳）且不被信封解包当错误；
 *   ③ 401 仍由全局拦截器处理（心跳本地不吞）；mustChangePassword 期间不启动；
 *      logout / stopHeartbeat 语义不变；
 *   ④ 对账：并发上限（默认 2）生效、退避指数增长且带抖动、启动抖动、4xx 不重试；
 *   ⑤ 产业字段：批量端点优先（按基线分组、一次多公司、单次 ≤50），失败回落逐个端点。
 *
 * 用法（仓库根目录，无需参数即可自动打包）：
 *   node tests/fix_verify/frontend/test_c3_heartbeat_backoff.mjs
 *   node tests/fix_verify/frontend/test_c3_heartbeat_backoff.mjs <已构建的 c3.mjs 路径>
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const read = (rel) => fs.readFileSync(path.resolve(repo, rel), "utf8");

// ---------- 打包：默认自动构建到系统临时目录（不污染仓库），也接受已构建产物路径 ----------
function buildBundle() {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "gipfel-c3-frontend-"));
  const ps1 = path.join(here, "bundle.ps1");
  const entry = path.join(here, "entries", "c3_entry.mjs");
  const shells = ["powershell.exe", "pwsh"];
  let lastError = null;
  for (const shell of shells) {
    // stdio: "inherit" —— 沙箱下 piped stdio 会被拒（见 bundle.ps1 顶部注释），因此继承父进程 stdio。
    const r = spawnSync(
      shell,
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, "-Entry", entry, "-Outfile", "c3.mjs"],
      { cwd: outDir, stdio: "inherit" },
    );
    if (!r.error) return path.join(outDir, "c3.mjs");
    lastError = r.error;
  }
  throw new Error(
    `打包失败：${lastError ? lastError.message : "未知错误"}（请手工执行 tests/fix_verify/frontend/bundle.ps1）`,
  );
}

const bundlePath = process.argv[2] ? path.resolve(process.argv[2]) : buildBundle();
const mod = await import(pathToFileURL(bundlePath).href);
const {
  api,
  COMPANY_FIELDS_BATCH_LIMIT,
  createPinia,
  getAccountItem,
  RECONCILE_CONCURRENCY,
  RECONCILE_MAX_ATTEMPTS,
  RECONCILE_RETRY_BASE_MS,
  RECONCILE_RETRY_MAX_MS,
  RECONCILE_START_JITTER_MS,
  normalizeCompanyFieldItems,
  reconcileCompanyFields,
  reconcileRetryDelayMs,
  reconcileStartDelayMs,
  reconcileWithRetry,
  runWithConcurrency,
  setActivePinia,
  useAuthStore,
} = mod;

// ===================== 测试骨架 =====================
let pass = 0;
let fail = 0;
const lines = [];

async function check(name, fn) {
  try {
    await fn();
    lines.push(`  [PASS] ${name}`);
    pass += 1;
  } catch (e) {
    lines.push(`  [FAIL] ${name}: ${String(e.message).split("\n")[0]}`);
    fail += 1;
  } finally {
    // 任一用例失败后也把可见性复位，避免级联失败
    setHidden(false);
  }
}

/** 去掉注释后的源码（静态断言只看真实代码，不被注释里的关键词误判）。 */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

// ===================== 可控桩：定时器 / 可见性 / axios adapter =====================
const clock = { intervals: [], cleared: [] };
let clockSeq = 1;
function installFakeClock() {
  const fakeSetInterval = (fn, ms) => {
    const id = (clockSeq += 1);
    clock.intervals.push({ id, ms, fn });
    return id;
  };
  const fakeClearInterval = (id) => {
    clock.cleared.push(id);
  };
  globalThis.setInterval = fakeSetInterval;
  globalThis.clearInterval = fakeClearInterval;
  globalThis.window.setInterval = fakeSetInterval;
  globalThis.window.clearInterval = fakeClearInterval;
}
installFakeClock();

function setHidden(v) {
  globalThis.document.hidden = v === true;
}
function fireVisibilityChange() {
  globalThis.window.dispatchEvent(new globalThis.CustomEvent("visibilitychange"));
}
const settle = (ms = 8) => new Promise((r) => setTimeout(r, ms));
async function waitFor(pred, timeoutMs = 800) {
  const deadline = Date.now() + timeoutMs;
  while (!pred() && Date.now() < deadline) await new Promise((r) => setTimeout(r, 1));
  return pred();
}

// 可控「墙上时钟」偏移：模拟「隐藏 ≥20s（或恰好 20s）才切回前台」，无需真的等 20s。
// 偏移只在派发 visibilitychange 的那一瞬生效（处理函数同步读取 Date.now），随后立刻归零，
// 以免影响 waitFor/settle 的超时判定与 memo 新鲜度判定。
let clockSkewMs = 0;
const realDateNow = Date.now;
Date.now = () => realDateNow() + clockSkewMs;

function enterHidden() {
  setHidden(true);
  fireVisibilityChange();
}
/** 切回前台；hiddenMs 为本次隐藏时长的模拟值（缺省用真实经过时间）。 */
function leaveHidden(hiddenMs) {
  clockSkewMs = hiddenMs == null ? 0 : hiddenMs;
  setHidden(false);
  fireVisibilityChange();
  clockSkewMs = 0;
}

/** 响应头归一化为小写键（axios 内部把 headers 规范化为 AxiosHeaders，键名大小写不保证）。 */
function headerObject(headers) {
  const out = {};
  if (!headers) return out;
  let src = headers;
  if (typeof headers.toJSON === "function") {
    try {
      src = headers.toJSON();
    } catch {
      src = headers;
    }
  }
  for (const [k, v] of Object.entries(src)) out[String(k).toLowerCase()] = v;
  return out;
}

const net = {
  me: [], // 心跳请求：{ params, headers, validateStatus }
  plain: [], // 非条件请求：{ url, params, headers, validateStatus }
  cf: [], // 公司产业字段请求：{ url, params }
  loginCalls: 0,
  mode: "ok", // ok | not-modified | unauthorized
  etag: '"v1"',
  batchFails: false,
};

function okResponse(config, data, headers = {}) {
  return { config, data: { code: 0, message: "ok", data }, status: 200, statusText: "OK", headers };
}

api.defaults.adapter = (config) => {
  const url = String(config.url || "");
  if (url.includes("/auth/login")) {
    net.loginCalls += 1;
    return Promise.resolve(
      okResponse(config, {
        token: "token-u1",
        user: {
          id: 1,
          username: "u1",
          role: "SUPER_ADMIN",
          permissions: [],
          companyScopes: [],
          mustChangePassword: false,
        },
      }),
    );
  }
  if (url.includes("/auth/me")) {
    const headers = headerObject(config.headers);
    net.me.push({ params: config.params, headers, validateStatus: config.validateStatus });
    if (net.mode === "unauthorized") {
      return Promise.reject({
        config,
        message: "Request failed with status code 401",
        response: {
          status: 401,
          statusText: "Unauthorized",
          data: { message: "您的账号已在其他设备登录，请重新登录" },
          headers: {},
          config,
        },
      });
    }
    if (net.mode === "not-modified" && headers["if-none-match"] === net.etag) {
      // 真实后端契约（§4.1）：命中 ETag → 304 + 空体 + 同一 ETag
      return Promise.resolve({
        config,
        data: "",
        status: 304,
        statusText: "Not Modified",
        headers: { etag: net.etag },
      });
    }
    return Promise.resolve(
      okResponse(
        config,
        { id: 1, tokenVersion: 1, isActive: true, mustChangePassword: false },
        { etag: net.etag },
      ),
    );
  }
  if (url.includes("/company-fields")) {
    const params = config.params || {};
    net.cf.push({ url, params });
    if (params.companyIds != null) {
      if (net.batchFails) {
        // 旧后端 / 未部署批量端点 → 404
        return Promise.reject({
          config,
          message: "Request failed with status code 404",
          response: { status: 404, statusText: "Not Found", data: { message: "未找到" }, headers: {}, config },
        });
      }
      const ids = String(params.companyIds).split(",");
      const companies = {};
      for (const id of ids) {
        const n = Number(id);
        companies[id] = {
          incremental: true,
          fields: [{ id: n * 10, value: `v${n}`, updatedAt: "2026-09-26T00:00:00Z" }],
          existingIds: [n * 10],
          serverTime: "2026-09-26T01:00:00Z",
        };
      }
      return Promise.resolve(
        okResponse(config, {
          companyIds: ids.map(Number),
          serverTime: "2026-09-26T01:00:00Z",
          companies,
          missing: [],
        }),
      );
    }
    const n = Number((url.match(/(\d+)$/) || [])[1]);
    return Promise.resolve(
      okResponse(config, {
        incremental: true,
        fields: [{ id: n * 10, value: `v${n}`, updatedAt: "2026-09-26T00:00:00Z" }],
        existingIds: [n * 10],
        serverTime: "2026-09-26T01:00:00Z",
      }),
    );
  }
  net.plain.push({
    url,
    params: config.params,
    headers: headerObject(config.headers),
    validateStatus: config.validateStatus,
  });
  return Promise.resolve(okResponse(config, [{ id: 1, name: "A公司" }]));
};

setActivePinia(createPinia());
const auth = useAuthStore();

let kicked = 0;
globalThis.window.addEventListener("auth:kicked", () => {
  kicked += 1;
});

/** 复位：可见、清计数、重新登录（同时清空心跳 ETag）。 */
async function freshLogin() {
  setHidden(false);
  auth.logout();
  net.me.length = 0;
  net.plain.length = 0;
  net.cf.length = 0;
  net.mode = "ok";
  net.etag = '"v1"';
  net.batchFails = false;
  await auth.login("u1", "pwd");
}

/** 触发最近一次排定的心跳定时器回调（等价于 20s/60s 到点），并等待其网络请求落到 adapter。 */
async function runHeartbeatTick() {
  const t = clock.intervals[clock.intervals.length - 1];
  assert.ok(t, "没有已排定的心跳定时器");
  const before = net.me.length;
  t.fn();
  await waitFor(() => net.me.length > before, 800);
  assert.ok(net.me.length > before, "心跳未触发 /auth/me 请求");
}

// ===================== 静态断言：源码接线 =====================
const requestTs = read("frontend/src/api/request.ts");
const authTs = read("frontend/src/stores/auth.ts");
const socketTs = read("frontend/src/realtime/socket.ts");
const resourceChangedTs = read("frontend/src/realtime/resource-changed.ts");

await check("auth.ts：可见 20s / 隐藏 60s 自适应 + visibilitychange 监听 + 补打门槛", () => {
  assert.match(authTs, /HEARTBEAT_INTERVAL_VISIBLE_MS\s*=\s*20\s*\*\s*1000/);
  assert.match(authTs, /HEARTBEAT_INTERVAL_HIDDEN_MS\s*=\s*60\s*\*\s*1000/);
  assert.match(authTs, /document\.hidden === true/);
  assert.match(authTs, /addEventListener\("visibilitychange", handleVisibilityChange\)/);
  // 补打门槛：记录隐藏起点，且仅当隐藏时长 ≥ 可见间隔（20s）时才补一次
  assert.match(authTs, /heartbeatHiddenAt = Date\.now\(\)/, "应记录进入隐藏的时刻");
  assert.match(authTs, /hiddenMs >= HEARTBEAT_INTERVAL_VISIBLE_MS/, "补打应以 ≥ 可见间隔为门槛");
});

await check("auth.ts：条件请求三要素（light=1 / If-None-Match / conditional）", () => {
  assert.match(authTs, /params:\s*\{\s*light:\s*1\s*\}/);
  assert.match(authTs, /headers\["If-None-Match"\]\s*=\s*heartbeatEtag/);
  assert.match(authTs, /conditional:\s*true/);
  assert.match(authTs, /cache:\s*false/);
});

await check("auth.ts：心跳本地不清登录态（401 仍归全局拦截器）", () => {
  const start = authTs.indexOf("async function beatHeartbeat()");
  const end = authTs.indexOf("function stopHeartbeat");
  assert.ok(start > -1 && end > start, "找不到 beatHeartbeat");
  const code = stripComments(authTs.slice(start, end));
  assert.ok(!/removeAccountItem/.test(code), "心跳不得清 token");
  assert.ok(!/auth:kicked/.test(code), "心跳不得自行派发 auth:kicked");
  assert.ok(!/#\/login/.test(code), "心跳不得跳登录页");
});

await check("auth.ts：mustChangePassword 守卫仍在 setInterval 之前（X-31 不变量）", () => {
  const start = authTs.indexOf("function startHeartbeat()");
  assert.ok(start > -1, "找不到 startHeartbeat");
  const body = authTs.slice(start, authTs.indexOf("\n  }", start));
  const guard = body.indexOf("mustChangePassword");
  const timer = body.indexOf("setInterval");
  assert.ok(guard > -1 && timer > -1 && guard < timer, "守卫必须在 setInterval 之前 return");
});

await check("request.ts：conditional 能力只作用于显式声明的调用", () => {
  assert.match(requestTs, /conditional\?:\s*boolean;/);
  assert.match(requestTs, /if \(config\.conditional\) \{/);
  assert.match(requestTs, /config\.validateStatus = \(status: number\) =>/);
  const condIdx = requestTs.indexOf("if (response.config?.conditional) {");
  assert.ok(condIdx > -1, "成功分支缺少 conditional 分支");
  assert.match(requestTs, /response\.status === 304/);
  const generalIdx = requestTs.indexOf("const decision = interpretResponse(response.data);", condIdx);
  assert.ok(generalIdx > condIdx, "conditional 分支必须早于通用信封判定");
});

await check("request.ts：对账并发上限 / 启动抖动 / 退避重试 / 批量端点接线", () => {
  assert.match(requestTs, /export const RECONCILE_CONCURRENCY = 2;/);
  assert.match(requestTs, /export const RECONCILE_MAX_ATTEMPTS = 3;/);
  assert.match(requestTs, /runWithConcurrency\(tasks\)/);
  assert.match(requestTs, /reconcileStartDelayMs\(\)/);
  assert.match(requestTs, /reconcileCompanyFields\(cfEntries\)/);
  assert.match(requestTs, /companyIds: entries\.map\(\(e\) => e\.companyId\)\.join\(","\)/);
  assert.match(requestTs, /`\/company-fields\/\$\{entry\.companyId\}`/);
  assert.ok(!/cols\.map\(async \(c\)/.test(requestTs), "对账扇出不得再是无上限 Promise.all");
  assert.match(requestTs, /new CustomEvent\("sync:reconciled"/);
  assert.match(requestTs, /await patchFullItems\([\s\S]{0,80}?collectionKey,/, "合并仍走 patchFullItems");
});

await check("socket.ts：重连退避 + 抖动参数显式（不再依赖库默认值）", () => {
  assert.match(socketTs, /reconnectionDelay:\s*2000/);
  assert.match(socketTs, /reconnectionDelayMax:\s*\d+/);
  assert.match(socketTs, /randomizationFactor:\s*0?\.\d+/);
});

await check("resource-changed.ts：重连仍走 reconcileAllIncremental（并发上限路径唯一入口）", () => {
  const idx = resourceChangedTs.indexOf('onRealtime("connect"');
  assert.ok(idx > -1, '找不到 onRealtime("connect")');
  assert.match(resourceChangedTs.slice(idx, idx + 800), /reconcileAllIncremental\(\)/);
});

// ===================== 动态断言：心跳 =====================
await check("心跳间隔：可见 20s，隐藏 60s（隐藏 > 可见）", async () => {
  await freshLogin();
  assert.equal(clock.intervals.length, 1, "登录后应排定一个心跳定时器");
  assert.equal(clock.intervals[0].ms, 20 * 1000, "可见时间隔应为 20s");

  setHidden(true);
  fireVisibilityChange();
  const hiddenMs = clock.intervals[clock.intervals.length - 1].ms;
  assert.equal(hiddenMs, 60 * 1000, "隐藏时间隔应为 60s");
  assert.ok(hiddenMs > clock.intervals[0].ms, `隐藏间隔必须大于可见间隔：${hiddenMs} vs ${clock.intervals[0].ms}`);

  // 隐藏期间不应额外打心跳
  const meBefore = net.me.length;
  await settle(15);
  assert.equal(net.me.length, meBefore, "切到隐藏不应立即补心跳");
});

await check("隐藏 <20s 切回：不补打心跳（频繁切窗口不产生额外请求）", async () => {
  // 上一用例结束时处于隐藏态，本次隐藏时长≈几百毫秒（远小于可见间隔 20s）
  const meBefore = net.me.length;
  leaveHidden(500);
  assert.equal(
    clock.intervals[clock.intervals.length - 1].ms,
    20 * 1000,
    "切回前台应恢复 20s 定时器",
  );
  await settle(25);
  assert.equal(net.me.length, meBefore, "隐藏 <20s 时不得补打心跳（门槛：仅 ≥20s 才补）");
});

await check("隐藏 ≥20s 切回：立即补一次心跳并恢复 20s 定时器", async () => {
  enterHidden();
  assert.equal(clock.intervals[clock.intervals.length - 1].ms, 60 * 1000, "隐藏时应切到 60s");
  const meBefore = net.me.length;
  leaveHidden(21 * 1000);
  assert.equal(
    clock.intervals[clock.intervals.length - 1].ms,
    20 * 1000,
    "切回前台应恢复 20s 定时器",
  );
  await waitFor(() => net.me.length > meBefore);
  assert.equal(net.me.length, meBefore + 1, "隐藏 ≥20s 时切回应立即补一次心跳");
});

await check("隐藏恰好 20s（等于可见间隔）切回：按 ≥ 门槛补一次", async () => {
  enterHidden();
  const meBefore = net.me.length;
  leaveHidden(20 * 1000);
  await waitFor(() => net.me.length > meBefore);
  assert.equal(net.me.length, meBefore + 1, "阈值取 ≥：隐藏正好 20s 也应补一次");
});

await check("条件请求：首次无 If-None-Match，200 存 ETag；随后带上并接受 304（不清登录态）", async () => {
  await freshLogin();
  net.mode = "ok";

  await runHeartbeatTick(); // tick1: 200
  assert.equal(net.me.length, 1);
  assert.deepEqual(net.me[0].params, { light: 1 }, "心跳应带 light=1（轻响应）");
  assert.equal(net.me[0].headers["if-none-match"], undefined, "首次心跳不应带 If-None-Match");
  assert.equal(typeof net.me[0].validateStatus, "function", "conditional 调用应逐调用改写 validateStatus");
  assert.equal(net.me[0].validateStatus(304), true, "conditional 调用应把 304 视为成功");
  assert.equal(net.me[0].validateStatus(200), true);
  assert.equal(net.me[0].validateStatus(500), false, "非法状态仍应判失败");

  net.mode = "not-modified";
  const clearedBefore = clock.cleared.length;
  const kickedBefore = kicked;
  const tokenBefore = getAccountItem("token");
  await runHeartbeatTick(); // tick2: 304
  assert.equal(net.me.length, 2);
  assert.equal(
    net.me[1].headers["if-none-match"],
    '"v1"',
    "第二次心跳必须带上上一次响应的 ETag",
  );
  assert.equal(kicked, kickedBefore, "304 不得派发 auth:kicked");
  assert.equal(getAccountItem("token"), tokenBefore, "304 不得清 token");
  assert.equal(auth.isLoggedIn, true, "304 后应保持登录态");
  assert.ok(auth.user, "304 不得清空 user");
  assert.equal(clock.cleared.length, clearedBefore, "304 不得停掉心跳定时器");
});

await check("conditional 调用直接返回 {status,headers,data}：304 不被信封解包当错误/空响应", async () => {
  net.mode = "not-modified";
  const r = await api.get("/auth/me", {
    cache: false,
    silent: true,
    conditional: true,
    params: { light: 1 },
    headers: { "If-None-Match": '"v1"' },
  });
  assert.equal(r.status, 304, "304 必须走成功分支（不得 reject）");
  assert.equal(r.data, null, "304 空体应归一为 null，不得崩溃");
  assert.equal(headerObject(r.headers)["etag"], '"v1"', "应保留响应头供读取 ETag");
});

await check("非条件请求行为不变：仍只返回解包数据、304 判定未被全局改写", async () => {
  await freshLogin();
  net.plain.length = 0;
  const v = await api.get("/companies", { cache: false });
  assert.deepEqual(v, [{ id: 1, name: "A公司" }], "非条件请求仍只返回解包后的业务数据");
  assert.equal(v.status, undefined, "非条件请求不得返回 {status,headers,data} 包装");
  assert.equal(net.plain.length, 1);
  assert.equal(
    net.plain[0].validateStatus(304),
    false,
    "非条件请求不得接受 304（逐调用的 validateStatus 覆写不得泄漏到其它请求）",
  );
  assert.equal(net.plain[0].validateStatus(200), true, "非条件请求的 2xx 判定不变");
  assert.equal(net.plain[0].validateStatus(500), false, "非条件请求的失败判定不变");
  assert.equal(net.plain[0].headers["if-none-match"], undefined);
});

await check("心跳 401 仍由全局拦截器处理：清 token + 派发 auth:kicked（本地不吞）", async () => {
  await freshLogin();
  net.mode = "unauthorized";
  const kickedBefore = kicked;
  await runHeartbeatTick();
  await waitFor(() => kicked > kickedBefore);
  assert.equal(kicked, kickedBefore + 1, "心跳 401 必须由全局拦截器派发 auth:kicked");
  assert.equal(getAccountItem("token"), null, "401 应清掉持久化 token");
  assert.equal(auth.token, "", "401 应清空内存 token");
  assert.equal(auth.user, null, "401 应清空 user");
  assert.ok(clock.cleared.length > 0, "踢出后应停掉心跳定时器");
});

await check("mustChangePassword 期间不启动心跳（可见性变化也不启动）", async () => {
  await freshLogin();
  const intervalsBefore = clock.intervals.length;
  auth.user.mustChangePassword = true;

  const clearedBefore = clock.cleared.length;
  auth.startHeartbeat();
  assert.equal(clock.intervals.length, intervalsBefore, "强制改密期间不得排定心跳");
  assert.ok(clock.cleared.length > clearedBefore, "startHeartbeat 仍应先停掉旧定时器");

  setHidden(true);
  fireVisibilityChange();
  setHidden(false);
  fireVisibilityChange();
  assert.equal(clock.intervals.length, intervalsBefore, "改密期间可见性变化不得启动心跳");

  auth.user.mustChangePassword = false;
  auth.startHeartbeat();
  assert.equal(clock.intervals.length, intervalsBefore + 1, "改密完成后应能重新启动心跳");
});

await check("logout / stopHeartbeat 语义不变：停定时器、登出后不再启动", async () => {
  await freshLogin();
  const clearedBefore = clock.cleared.length;
  auth.stopHeartbeat();
  assert.ok(clock.cleared.length > clearedBefore, "stopHeartbeat 应清掉定时器");
  clock.cleared.length = 0;
  auth.stopHeartbeat();
  assert.equal(clock.cleared.length, 0, "stopHeartbeat 幂等（无定时器时不清任何东西）");

  await freshLogin();
  const intervalsBefore = clock.intervals.length;
  auth.logout();
  assert.equal(auth.token, "", "logout 应清 token");
  assert.equal(auth.user, null, "logout 应清 user");
  assert.equal(getAccountItem("token"), null, "logout 应清持久化 token");
  auth.startHeartbeat();
  assert.equal(clock.intervals.length, intervalsBefore, "登出后 startHeartbeat 不得再排定定时器");
});

// ===================== 动态断言：退避 / 抖动 / 并发上限 =====================
await check("退避纯函数：指数增长、封顶、带抖动且落在 [0.5x,1.5x]", () => {
  const fixed = () => 0.5; // 抖动系数 = 1.0
  assert.equal(reconcileRetryDelayMs(0, fixed), RECONCILE_RETRY_BASE_MS);
  assert.equal(reconcileRetryDelayMs(1, fixed), RECONCILE_RETRY_BASE_MS * 2);
  assert.equal(reconcileRetryDelayMs(2, fixed), RECONCILE_RETRY_BASE_MS * 4);
  assert.equal(reconcileRetryDelayMs(3, fixed), RECONCILE_RETRY_BASE_MS * 8);
  assert.ok(RECONCILE_RETRY_MAX_MS > RECONCILE_RETRY_BASE_MS, "退避上限应大于基数");
  assert.equal(reconcileRetryDelayMs(20, fixed), RECONCILE_RETRY_MAX_MS, "应封顶在 RECONCILE_RETRY_MAX_MS");
  assert.ok(
    reconcileRetryDelayMs(4, fixed) === RECONCILE_RETRY_MAX_MS,
    "指数增长到上限后保持恒定",
  );
  // 抖动：同一 attempt 在不同随机数下不同，且区间为 [0.5x, 1.5x]
  assert.equal(reconcileRetryDelayMs(1, () => 0), 500);
  assert.equal(reconcileRetryDelayMs(1, () => 1), 1500);
  const seen = new Set();
  for (let i = 0; i < 60; i += 1) {
    const d = reconcileRetryDelayMs(2);
    seen.add(d);
    assert.ok(d >= 1000 && d <= 3000, `抖动后应落在 [1000,3000]，实际 ${d}`);
  }
  assert.ok(seen.size > 1, "抖动应产生不同取值（全场不在同一毫秒重连）");
});

await check("启动抖动纯函数：∈ [0, RECONCILE_START_JITTER_MS)", () => {
  assert.ok(RECONCILE_START_JITTER_MS > 0, "应存在启动抖动窗口");
  assert.equal(reconcileStartDelayMs(() => 0), 0);
  assert.equal(reconcileStartDelayMs(() => 1), RECONCILE_START_JITTER_MS);
  for (let i = 0; i < 30; i += 1) {
    const d = reconcileStartDelayMs();
    assert.ok(d >= 0 && d <= RECONCILE_START_JITTER_MS, `启动抖动越界：${d}`);
  }
});

await check("并发上限：runWithConcurrency 默认 ≤2 在途，单任务失败不影响其余", async () => {
  assert.equal(RECONCILE_CONCURRENCY, 2, "默认并发上限应为 2");
  let inFlight = 0;
  let maxInFlight = 0;
  let done = 0;
  const tasks = Array.from({ length: 7 }, (_, i) => async () => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((r) => setTimeout(r, 5));
    inFlight -= 1;
    done += 1;
    if (i === 3) throw new Error("boom"); // 单个集合失败必须静默且不影响其余
  });
  await runWithConcurrency(tasks);
  assert.equal(done, 7, "单任务失败不得中断其余任务");
  assert.equal(inFlight, 0, "全部任务结束后不应残留在途计数");
  assert.equal(maxInFlight, RECONCILE_CONCURRENCY, `并发峰值应为 ${RECONCILE_CONCURRENCY}，实际 ${maxInFlight}`);
});

await check("reconcileWithRetry：网络错误退避重试至成功；4xx 不重试", async () => {
  const realTimeout = globalThis.setTimeout;
  const realRandom = Math.random;
  const delays = [];
  globalThis.Math.random = () => 0.5; // 抖动固定 1.0x，便于断言指数序列
  globalThis.setTimeout = (fn, ms) => {
    delays.push(ms);
    return realTimeout(fn, 0);
  };
  try {
    let attempts = 0;
    const v = await reconcileWithRetry(async () => {
      attempts += 1;
      if (attempts < RECONCILE_MAX_ATTEMPTS) throw new Error("Network Error");
      return "ok";
    });
    assert.equal(v, "ok", "重试后应成功");
    assert.equal(attempts, RECONCILE_MAX_ATTEMPTS, "应按 RECONCILE_MAX_ATTEMPTS 重试");
    assert.deepEqual(
      delays,
      [RECONCILE_RETRY_BASE_MS, RECONCILE_RETRY_BASE_MS * 2],
      `退避应指数增长，实际 ${JSON.stringify(delays)}`,
    );

    delays.length = 0;
    attempts = 0;
    const v404 = await reconcileWithRetry(async () => {
      attempts += 1;
      const e = new Error("Not Found");
      e.response = { status: 404 };
      throw e;
    });
    assert.equal(v404, null, "确定性失败应静默返回 null");
    assert.equal(attempts, 1, "4xx 不应退避重试（避免放大请求与日志）");
    assert.equal(delays.length, 0, "4xx 不应产生退避等待");
  } finally {
    globalThis.setTimeout = realTimeout;
    globalThis.Math.random = realRandom;
  }
});

// ===================== 动态断言：产业字段批量端点 =====================
const cfEntries = (ids, baseline) =>
  ids.map((id) => ({ collectionKey: `companyField|companyId=${id}`, companyId: id, baseline }));

await check("产业字段：同基线多公司合并为一次批量请求（按基线分组）", async () => {
  net.cf.length = 0;
  net.batchFails = false;
  await reconcileCompanyFields([...cfEntries([1, 2], "b1"), ...cfEntries([3], "b2")]);

  const batches = net.cf.filter((c) => c.params.companyIds != null);
  const singles = net.cf.filter((c) => /\/company-fields\/\d+/.test(c.url));
  assert.equal(batches.length, 1, "同一基线的两家公司应合并为 1 次批量请求");
  assert.equal(batches[0].url, "/company-fields", "批量端点路径必须是 /company-fields（§4.2）");
  assert.equal(batches[0].params.companyIds, "1,2", "companyIds 应为逗号分隔（§4.2）");
  assert.equal(batches[0].params.updatedAfter, "b1", "批量请求应带该分组的基线");
  assert.deepEqual(singles.map((c) => c.url), ["/company-fields/3"], "单公司分组直接走单点端点");
  assert.ok(
    !net.cf.some((c) => c.url === "/company-fields/1" || c.url === "/company-fields/2"),
    "批量成功时不得再逐个请求同一分组的公司",
  );
});

await check("产业字段：批量端点失败（404）→ 回落逐个 /company-fields/{cid}", async () => {
  net.cf.length = 0;
  net.batchFails = true;
  await reconcileCompanyFields(cfEntries([1, 2, 3], "b1"));

  const batches = net.cf.filter((c) => c.params.companyIds != null);
  const singles = net.cf.filter((c) => /\/company-fields\/\d+/.test(c.url));
  assert.equal(batches.length, 1, "应优先尝试批量端点");
  assert.deepEqual(
    singles.map((c) => c.url).sort(),
    ["/company-fields/1", "/company-fields/2", "/company-fields/3"],
    "批量失败必须回落逐个端点，对账不丢",
  );
  assert.ok(singles.every((c) => c.params.updatedAfter === "b1"), "回落请求各自带原基线");
  net.batchFails = false;
});

await check("产业字段：单次批量不超过 COMPANY_FIELDS_BATCH_LIMIT（超出分批）", async () => {
  assert.equal(COMPANY_FIELDS_BATCH_LIMIT, 50, "批量上限应与设计说明 §4.2 的建议值一致");
  net.cf.length = 0;
  net.batchFails = false;
  const many = Array.from({ length: COMPANY_FIELDS_BATCH_LIMIT + 10 }, (_, i) => i + 1);
  await reconcileCompanyFields(cfEntries(many, "b1"));
  const batches = net.cf.filter((c) => c.params.companyIds != null);
  assert.deepEqual(
    batches.map((c) => String(c.params.companyIds).split(",").length),
    [COMPANY_FIELDS_BATCH_LIMIT, 10],
    "超过上限应分批，而不是一次请求全部公司",
  );
});

await check("产业字段：元素无 id 键 → 以 industryFieldId 归一为 id（否则 patchFullItems 会清空本地副本）", () => {
  const src = [
    { industryFieldId: 7, fieldKey: "location", value: "北京" },
    { id: 9, industryFieldId: 9, fieldKey: "cash", value: "1" },
    { industryFieldId: null, fieldKey: "x" },
    null,
  ];
  const out = normalizeCompanyFieldItems(src);
  assert.equal(out.length, 4, "不得丢弃元素（null 也原样保留）");
  assert.equal(out[0].id, 7, "无 id 键时应补 id = industryFieldId");
  assert.equal(out[0].industryFieldId, 7, "industryFieldId 原样保留");
  assert.equal(out[0].value, "北京", "其余字段原样保留");
  assert.equal(out[1].id, 9, "已有 id 时应原样保留");
  assert.equal(out[2].id, undefined, "industryFieldId 为空时不得编造 id");
  assert.equal(out[3], null, "非对象元素原样透传");
  assert.notEqual(out[0], src[0], "应返回副本，不就地改写后端响应对象");
  assert.equal(src[0].id, undefined, "不得污染原始响应对象");
});

await check("产业字段：三处合并路径都走归一（含批量回落单点）", () => {
  const calls = requestTs.match(/normalizeCompanyFieldItems\(/g) || [];
  assert.ok(
    calls.length >= 4,
    `定义 + 全量存储 + 正常增量 + 对账（批量/回落）都应归一，实际出现 ${calls.length} 次`,
  );
  assert.match(
    requestTs,
    /patchFullItems\(\s*collectionKey,\s*normalizeCompanyFieldItems\(/s,
    "对账合并必须先归一（元素无 id 键）",
  );
  assert.match(requestTs, /const fields: unknown\[\] = normalizeCompanyFieldItems\(/, "全量存储也要归一");
});

await check("产业字段：同一基线同一公司不重复请求（去重，避免触碰后端 50 家上限）", async () => {
  net.cf.length = 0;
  net.batchFails = false;
  await reconcileCompanyFields([
    { collectionKey: "companyField|companyId=1", companyId: 1, baseline: "b1" },
    { collectionKey: "companyField|companyId=1", companyId: 1, baseline: "b1" },
    { collectionKey: "companyField|companyId=2", companyId: 2, baseline: "b1" },
  ]);
  const batches = net.cf.filter((c) => c.params.companyIds != null);
  assert.deepEqual(batches.map((c) => c.params.companyIds), ["1,2"], "重复公司应去重后合并为一次批量请求");
});

// ===================== 结果 =====================
console.log("C3 自适应条件心跳 + 重连退避/并发上限 + 产业字段批量端点：");
for (const l of lines) console.log(l);
console.log(`\nPASS=${pass} FAIL=${fail}`);
process.exit(fail === 0 ? 0 : 1);
