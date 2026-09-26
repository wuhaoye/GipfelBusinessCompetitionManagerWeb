/**
 * 预计金额用例入口：真实 frontend/src/views/stocks/estAmount.ts。
 *
 * 供 test_t01_est_amount.mjs（T-01）使用；该用例另外自行从 StockMarketView.vue 源码
 * 抽取 estAmount 计算体做一致性对照，两者都应得到同一结果。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\est_amount_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\estAmount.mjs
 *   node tests\fix_verify\frontend\test_t01_est_amount.mjs tests\fix_verify\frontend\.build\estAmount.mjs
 */
import { computeEstAmount } from "@/views/stocks/estAmount";

export { computeEstAmount };
