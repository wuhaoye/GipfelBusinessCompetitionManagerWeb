/**
 * 载具「可通过路径类型」出入参用例入口：真实 frontend/src/views/data-management/vehiclePathTypes.ts。
 *
 * 供 test_w02_vehicle_path_types.mjs（W-02）使用；该用例另外静态核对了
 * VehiclesManager.vue 的提交/回填是否使用后端契约字段 `vehiclePathTypes`。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\vehicle_path_types_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\vehiclePathTypes.mjs
 *   node tests\fix_verify\frontend\test_w02_vehicle_path_types.mjs tests\fix_verify\frontend\.build\vehiclePathTypes.mjs
 */
import { toPathTypeIds, toVehiclePathTypes } from "@/views/data-management/vehiclePathTypes";

export { toPathTypeIds, toVehiclePathTypes };
