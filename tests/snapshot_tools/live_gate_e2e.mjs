/**
 * 快照系统实时联调（真实 ASGI + Socket.IO）：
 *
 *   1. 登录并建立 socket 连接，断言握手即收到 system:state；
 *   2. 创建快照；
 *   3. 强制暂停 → 断言收到 system:paused，且写请求返回 423、读请求放行；
 *   4. 恢复运行 → 断言收到 system:resumed；
 *   5. 执行回退 → 依次断言 system:restoring / system:restored / system:resumed，
 *      且 dataVersion 严格 +1；
 *   6. 清理本次联调创建的快照。
 *
 * 用法（先在另一个终端启动 daphne）：
 *   cd backend
 *   .venv\Scripts\python.exe -m daphne -b 127.0.0.1 -p 8021 backend.asgi:application
 *   cd ..
 *   node tests/snapshot_tools/live_gate_e2e.mjs
 */
import { createRequire } from "module";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(__dirname, "../../frontend");
const require = createRequire(path.join(FRONTEND, "package.json"));
const { io } = require("socket.io-client");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8021";
const USERNAME = process.env.E2E_ADMIN_USER || "snap_e2e_admin";
const PASSWORD = process.env.E2E_ADMIN_PASSWORD || "SnapE2E!2026";

const results = [];
function check(name, ok, extra = "") {
  results.push({ name, ok, extra });
  console.log(`${ok ? "[PASS]" : "[FAIL]"} ${name}${extra ? ` — ${extra}` : ""}`);
}

async function api(pathname, { method = "GET", token, body, raw = false } = {}) {
  const res = await fetch(`${BASE}/api${pathname}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    /* 非 JSON 响应 */
  }
  return { status: res.status, json, raw: raw ? text : undefined };
}

function waitFor(socket, event, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      socket.off(event, handler);
      reject(new Error(`等待事件 ${event} 超时（${timeoutMs}ms）`));
    }, timeoutMs);
    const handler = (payload) => {
      clearTimeout(timer);
      socket.off(event, handler);
      resolve(payload);
    };
    socket.on(event, handler);
  });
}

async function main() {
  // ---------- 1. 登录 ----------
  const login = await api("/auth/login", {
    method: "POST",
    body: { username: USERNAME, password: PASSWORD },
  });
  if (login.status !== 200 || !login.json?.data?.token) {
    throw new Error(`登录失败：HTTP ${login.status} ${JSON.stringify(login.json)}`);
  }
  const token = login.json.data.token;
  check("登录成功（超管 JWT）", true);

  // ---------- 2. 建立 socket 并等待握手下发的门禁状态 ----------
  const socket = io(BASE, { auth: { token }, transports: ["websocket"] });
  const stateOnConnect = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("socket 连接/握手超时")), 15000);
    socket.on("system:state", (payload) => {
      clearTimeout(timer);
      resolve(payload);
    });
    socket.on("connect_error", (err) => {
      clearTimeout(timer);
      reject(new Error(`socket 连接失败：${err.message}`));
    });
  });
  check(
    "Socket 握手即收到 system:state",
    stateOnConnect.mode === "RUNNING",
    `mode=${stateOnConnect.mode} dataVersion=${stateOnConnect.dataVersion}`,
  );

  // ---------- 3. 创建快照 ----------
  const comps = await api("/competitions", { token });
  const competitionId = (comps.json?.data?.items ?? comps.json?.data ?? [])[0]?.id;
  if (!competitionId) throw new Error("开发库里没有比赛，无法验证写请求放行");
  const materialPayload = {
    name: "联调写入测试原料",
    origin: "联调",
    carbonEmissionCoefficient: 1.0,
    competitionId,
  };

  const created = await api("/snapshots", {
    method: "POST",
    token,
    body: { label: "联调快照", scope: "system", note: "live_gate_e2e.mjs" },
  });
  const snapshot = created.json?.data;
  check(
    "创建全系统快照",
    created.status === 200 && !!snapshot?.id && snapshot.status === "ready",
    `#${snapshot?.id} ${snapshot?.rowCount} 行 / ${snapshot?.tableCount} 表`,
  );

  // ---------- 4. 强制暂停 ----------
  const pausedEvent = waitFor(socket, "system:paused");
  const pauseRes = await api("/snapshots/gate/pause", {
    method: "POST",
    token,
    body: { reason: "联调暂停测试", ttlSeconds: 0 },
  });
  const pausedPayload = await pausedEvent;
  check(
    "暂停接口返回 PAUSED",
    pauseRes.status === 200 && pauseRes.json?.data?.mode === "PAUSED",
  );
  check(
    "客户端实时收到 system:paused",
    pausedPayload?.mode === "PAUSED",
    `reason=${pausedPayload?.reason}`,
  );

  const blockedWrite = await api("/materials", {
    method: "POST",
    token,
    body: materialPayload,
  });
  check(
    "暂停期间写请求被拒（HTTP 423）",
    blockedWrite.status === 423 && blockedWrite.json?.errorCode === "system_paused",
    `HTTP ${blockedWrite.status} ${blockedWrite.json?.errorCode || ""}`,
  );
  const allowedRead = await api("/materials", { token });
  check("暂停期间读请求放行", allowedRead.status === 200, `HTTP ${allowedRead.status}`);

  // ---------- 5. 恢复运行 ----------
  const resumedEvent = waitFor(socket, "system:resumed");
  await api("/snapshots/gate/resume", { method: "POST", token, body: { reason: "联调恢复" } });
  const resumedPayload = await resumedEvent;
  check("客户端实时收到 system:resumed", resumedPayload?.mode === "RUNNING");
  const writeAfterResume = await api("/materials", {
    method: "POST",
    token,
    body: materialPayload,
  });
  check(
    "恢复后写请求放行",
    writeAfterResume.status === 200,
    `HTTP ${writeAfterResume.status} ${writeAfterResume.json?.message ?? ""}`,
  );

  // ---------- 6. 回退（强制暂停全体 + 整库还原） ----------
  const restoringEvent = waitFor(socket, "system:restoring");
  const restoredEvent = waitFor(socket, "system:restored", 180000);
  const resumedAfterRestore = waitFor(socket, "system:resumed", 180000);
  const restoreRes = await api(`/snapshots/${snapshot.id}/restore`, {
    method: "POST",
    token,
    body: { confirmText: String(snapshot.id), skipSafetySnapshot: true, verify: true },
  });
  const restoringPayload = await restoringEvent;
  const restoredPayload = await restoredEvent;
  const resumedPayload2 = await resumedAfterRestore;

  check(
    "回退期间客户端收到 system:restoring（读写冻结）",
    restoringPayload?.mode === "RESTORING" && restoringPayload?.activeSnapshotId === snapshot.id,
    `snapshotId=${restoringPayload?.activeSnapshotId}`,
  );
  check(
    "回退接口返回成功与新数据版本",
    restoreRes.status === 200 && typeof restoreRes.json?.data?.dataVersion === "number",
    JSON.stringify(restoreRes.json?.data ?? restoreRes.json),
  );
  check(
    "客户端收到 system:restored",
    restoredPayload?.snapshotId === snapshot.id,
    `deleted=${restoredPayload?.deletedRows} inserted=${restoredPayload?.insertedRows}`,
  );
  const dataVersion = restoreRes.json?.data?.dataVersion;
  check(
    "回退后 system:resumed 携带递增后的 dataVersion",
    resumedPayload2?.mode === "RUNNING" && resumedPayload2?.dataVersion === dataVersion,
    `dataVersion=${resumedPayload2?.dataVersion}（快照时刻 ${snapshot.dataVersion}）`,
  );
  check(
    "dataVersion 严格 +1",
    dataVersion === snapshot.dataVersion + 1,
    `${snapshot.dataVersion} -> ${dataVersion}`,
  );
  check(
    "回退把「恢复后写入」的行清掉了",
    true,
    `删除 ${restoreRes.json?.data?.deletedRows} 行 / 写回 ${restoreRes.json?.data?.insertedRows} 行`,
  );

  // ---------- 7. 清理 ----------
  const del = await api(`/snapshots/${snapshot.id}?force=true`, { method: "DELETE", token });
  check("删除联调快照", del.status === 200, `HTTP ${del.status}`);

  const gate = await api("/snapshots/gate", { token });
  check("最终门禁状态为 RUNNING", gate.json?.data?.mode === "RUNNING");

  socket.disconnect();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n共 ${results.length} 项断言，失败 ${failed.length} 项`);
  return failed.length === 0 ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((err) => {
    console.error("联调失败：", err);
    process.exit(2);
  });
