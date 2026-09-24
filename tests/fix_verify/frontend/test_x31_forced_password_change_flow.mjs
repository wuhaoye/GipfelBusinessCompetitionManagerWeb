/**
 * X-31 验证：强制改密（must_change_password）流程不得再被 20s 会话心跳打断。
 *
 * 真机事故（Debian 13 全新部署）：
 *   超管首次登录后 must_change_password=true；前端登录成功即启动 20s 心跳打 /auth/me；
 *   后端门禁当时只放行 /api/auth/change-password → 心跳收到 401 → 全局 401 拦截器把它当成
 *   「会话过期」→ 清 token / 跳登录 / 派发 auth:kicked；用户随后提交「修改初始密码」时
 *   请求已不带 Authorization → 后端 401「登录已过期，请重新登录」→ 初始密码永远改不掉。
 *
 * 本用例做**跨层静态不变量**核对（三层缺一不可）：
 *   ① 后端 auth 层：/api/auth/me 在强制改密豁免名单内；
 *   ② 前端拦截器：401 按 errorCode=must_change_password 识别门禁，且早于清 token；
 *   ③ 前端会话层：强制改密期间不启动心跳；改密前若登录态已空则先用旧密码恢复会话。
 *
 * 用法（仓库根目录）：
 *   node tests/fix_verify/frontend/test_x31_forced_password_change_flow.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../../..");
const read = (rel) => fs.readFileSync(path.resolve(repo, rel), "utf8");

const requestTs = read("frontend/src/api/request.ts");
const authTs = read("frontend/src/stores/auth.ts");
const loginVue = read("frontend/src/views/login/LoginView.vue");
const authPy = read("backend/apps/auth/authentication.py");

let pass = 0;
let fail = 0;
const cases = [];

function check(name, fn) {
  try {
    fn();
    cases.push(`  [PASS] ${name}`);
    pass += 1;
  } catch (e) {
    cases.push(`  [FAIL] ${name}\n         ${e.message}`);
    fail += 1;
  }
}

function functionBody(source, signature) {
  const start = source.indexOf(signature);
  assert.ok(start > -1, `找不到 ${signature}`);
  const rest = source.slice(start);
  const end = rest.indexOf("\n  }");
  return rest.slice(0, end > -1 ? end : rest.length);
}

// ---------- ① 后端：/api/auth/me 必须在强制改密豁免名单里 ----------
check("后端把 /api/auth/me 列入强制改密豁免（心跳/改密弹窗可读自身资料）", () => {
  const m = authPy.match(/_CHANGE_PASSWORD_PATHS\s*=\s*\(([\s\S]*?)\)/);
  assert.ok(m, "找不到 _CHANGE_PASSWORD_PATHS 定义");
  assert.match(m[1], /\/api\/auth\/change-password/);
  assert.match(m[1], /\/api\/auth\/me/, "缺少 /api/auth/me —— 心跳 401 会再次清空登录态");
});

// ---------- ② 前端拦截器：门禁 401 不得清 token / 跳登录 / 派发 auth:kicked ----------
check("401 拦截器按 errorCode 识别门禁，且位于清 token 之前", () => {
  const guardIdx = requestTs.indexOf('errorCode === "must_change_password"');
  assert.ok(
    guardIdx > -1,
    "未按 errorCode 判断：data.code 是 HTTP 状态码（401），拿它比对会永不命中",
  );
  const clearIdx = requestTs.indexOf('removeAccountItem("token")');
  assert.ok(clearIdx > -1, "找不到清 token 的位置");
  assert.ok(guardIdx < clearIdx, "门禁判断必须早于清 token / 跳登录页");

  const branch = requestTs.slice(guardIdx, guardIdx + 420);
  assert.ok(!branch.includes("removeAccountItem"), "门禁分支里不得清 token");
  assert.ok(!branch.includes("#/login"), "门禁分支里不得跳登录页");
  assert.ok(!branch.includes("auth:kicked"), "门禁分支里不得派发 auth:kicked");
  assert.ok(branch.includes("reject"), "门禁分支应原样 reject，交由调用方提示");
});

// ---------- ③ 会话层：强制改密期间不启动心跳 ----------
check("startHeartbeat 在 mustChangePassword 期间不启动", () => {
  const body = functionBody(authTs, "function startHeartbeat()");
  assert.match(
    body,
    /mustChangePassword/,
    "startHeartbeat 缺少 mustChangePassword 守卫：门禁期间心跳会打 /auth/me 并可能清空会话",
  );
  const guardIdx = body.indexOf("mustChangePassword");
  const timerIdx = body.indexOf("setInterval");
  assert.ok(timerIdx > -1, "找不到 setInterval");
  assert.ok(guardIdx < timerIdx, "守卫必须在 setInterval 之前 return");
});

// ---------- ③ 会话层：改密前登录态可能已被清空 → 先用旧密码恢复 ----------
check("changePassword 支持用户名参数并在无 token 时先恢复会话", () => {
  const body = functionBody(authTs, "async function changePassword(");
  assert.match(body, /username\?: string/, "changePassword 应接受可选 username（登录页传入）");
  assert.match(body, /if \(!token\.value && loginName\)/, "缺少「无 token 先重登」的恢复分支");
  assert.match(body, /await login\(loginName, oldPassword\)/, "恢复会话必须用刚输入的旧密码");
});

check("改密 401 重试分支不再依赖可能已被清空的 user", () => {
  const body = functionBody(authTs, "async function changePassword(");
  assert.match(
    body,
    /const retryName = user\.value\?\.username \|\| loginName/,
    "重试分支应回退到入参 username（logout 会清空 user）",
  );
});

// ---------- 登录页：把用户名传给改密接口 + 预填原密码 ----------
check("LoginView 提交改密时带上用户名", () => {
  assert.match(
    loginVue,
    /changePassword\(changeForm\.oldPassword,\s*changeForm\.newPassword,\s*form\.username\)/,
    "登录页未把用户名传给 changePassword",
  );
});

check("LoginView 命中强制改密时预填原密码", () => {
  assert.match(
    loginVue,
    /changeForm\.oldPassword = form\.password/,
    "未预填刚输入的初始密码：用户需重复手输，容易输错成「原密码不正确」",
  );
});

// ---------- 后端：401 必须带机器码，前端才有得判 ----------
check("异常处理器保留 401 的机器码（errorCode）", () => {
  const exceptionsPy = read("backend/apps/common/exceptions.py");
  const responsePy = read("backend/apps/common/response.py");
  assert.match(exceptionsPy, /_machine_code\(/, "exceptions.py 未提取机器码");
  assert.match(responsePy, /error_code/, "response.error() 未支持 error_code 字段");
  assert.match(responsePy, /errorCode/, "响应体应输出 errorCode 字段");
});

console.log(cases.join("\n"));
console.log(`\nPASS=${pass} FAIL=${fail}`);
process.exit(fail === 0 ? 0 : 1);
