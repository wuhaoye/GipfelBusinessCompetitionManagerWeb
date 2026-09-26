/**
 * 仪表盘布局存储用例入口：真实 frontend/src/components/dashboard/layoutStorage.ts。
 *
 * 供 test_m01_widget_layout_preserved.mjs（未注册控件不得被回写删掉）使用。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\layout_storage_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\layoutStorage.mjs
 *   node tests\fix_verify\frontend\test_m01_widget_layout_preserved.mjs tests\fix_verify\frontend\.build\layoutStorage.mjs
 */
import {
  mergeLayoutForSave,
  migrateLegacySizes,
  splitStoredLayout,
} from "@/components/dashboard/layoutStorage";

export { mergeLayoutForSave, migrateLegacySizes, splitStoredLayout };
