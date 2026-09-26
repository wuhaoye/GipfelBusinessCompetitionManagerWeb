/**
 * 列表并发守卫用例入口：真实 frontend/src/composables/useLatestRequest.ts。
 *
 * 供两个用例复用：
 *   - test_v09_list_race_guards.mjs   （MapsManager / ContractManageView / TechTreeManager / FuelManager）
 *   - test_w08_warehouses_race.mjs    （仓库列表）
 *
 * 注意：该模块 `import { ref } from "vue"`，**必须**用 `--bundle` 打包（bundle.ps1 默认就是
 * `--bundle --platform=browser`，会把 vue 一起打进来），否则 Node 侧 import 会
 * ERR_MODULE_NOT_FOUND: Cannot find package 'vue'（这两个用例头部注释里的旧命令未带 --bundle）。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\use_latest_request_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\useLatestRequest.mjs
 *   node tests\fix_verify\frontend\test_v09_list_race_guards.mjs tests\fix_verify\frontend\.build\useLatestRequest.mjs
 *   node tests\fix_verify\frontend\test_w08_warehouses_race.mjs tests\fix_verify\frontend\.build\useLatestRequest.mjs
 */
import { useLatestRequest } from "@/composables/useLatestRequest";

export { useLatestRequest };
