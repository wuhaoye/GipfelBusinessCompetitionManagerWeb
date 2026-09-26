/**
 * 时间格式化用例入口：真实 frontend/src/utils/format.ts。
 *
 * 供 test_format_time.mjs（F-08）使用；该用例自己会设 `process.env.TZ = "Asia/Shanghai"`。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\format_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\format.mjs
 *   node tests\fix_verify\frontend\test_format_time.mjs tests\fix_verify\frontend\.build\format.mjs
 */
import { formatTime } from "@/utils/format";

export { formatTime };
