/**
 * 响应体解释（信封解包）用例入口：真实 frontend/src/api/envelope.ts。
 *
 * 供 test_response_envelope.mjs（F-01）使用；该模块被 request.ts 的响应拦截器共用，
 * 因此这里跑的就是线上同一份判定逻辑。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\envelope_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\envelope.mjs
 *   node tests\fix_verify\frontend\test_response_envelope.mjs tests\fix_verify\frontend\.build\envelope.mjs
 */
import { interpretResponse, isApiEnvelope, isBinaryPayload } from "@/api/envelope";

export { interpretResponse, isApiEnvelope, isBinaryPayload };
