/**
 * C3 验证入口：真实 auth store（自适应条件心跳）+ 真实 request 层
 * （重连对账并发上限 / 指数退避 / 抖动 / 产业字段批量端点）。
 *
 * 由 tests/fix_verify/frontend/bundle.ps1 打包成单个 ESM 后用 Node import；
 * browser.mjs 必须排在最前：其余模块顶层会读 window / localStorage。
 *
 * 只有 axios adapter、定时器（setInterval/clearInterval）与 document.hidden 由用例替换，
 * 其余（心跳调度、条件请求拦截器、对账调度器）都是 frontend/src 的真实实现。
 */
import "../stubs/browser.mjs";

import { createPinia, setActivePinia } from "pinia";

import api from "@/api";
import {
  COMPANY_FIELDS_BATCH_LIMIT,
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
} from "@/api/request";
import { useAuthStore } from "@/stores/auth";
import {
  getAccountItem,
  removeAccountItem,
  setAccountItem,
  setActiveUser,
} from "@/utils/accountStorage";
import { browserStub } from "../stubs/browser.mjs";

export {
  api,
  browserStub,
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
  removeAccountItem,
  runWithConcurrency,
  setAccountItem,
  setActivePinia,
  setActiveUser,
  useAuthStore,
};
