/**
 * 验收探针专用打包入口（tests/ops_check）：只导出独立复算需要的真实前端模块。
 * 由 tests/fix_verify/frontend/bundle.ps1 打包（复用其 alias/桩配置，不复制逻辑）。
 */
import "../../fix_verify/frontend/stubs/browser.mjs";

import { createPinia, setActivePinia } from "pinia";

import api from "@/api";
import {
  COMPANY_FIELDS_BATCH_LIMIT,
  RECONCILE_CONCURRENCY,
  RECONCILE_MAX_ATTEMPTS,
  RECONCILE_RETRY_BASE_MS,
  RECONCILE_RETRY_MAX_MS,
  RECONCILE_START_JITTER_MS,
  reconcileCompanyFields,
  reconcileRetryDelayMs,
  reconcileStartDelayMs,
  runWithConcurrency,
} from "@/api/request";
import { useAuthStore } from "@/stores/auth";
import { getAccountItem, setAccountItem, setActiveUser } from "@/utils/accountStorage";
import { browserStub } from "../../fix_verify/frontend/stubs/browser.mjs";

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
  reconcileCompanyFields,
  reconcileRetryDelayMs,
  reconcileStartDelayMs,
  runWithConcurrency,
  setAccountItem,
  setActivePinia,
  setActiveUser,
  useAuthStore,
};
