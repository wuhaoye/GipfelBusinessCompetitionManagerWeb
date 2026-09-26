/**
 * 分页/降维用例入口：真实 frontend/src/api/localPaging.ts。
 *
 * 供两个用例复用：
 *   - test_list_paging.mjs            （A-01 / V-04 / W-05）
 *   - test_f12_page_bounds.mjs        （F-12）
 *
 * 复现命令（仓库根目录；-Outfile 传相对路径，bundle.ps1 用 Join-Path 拼 cwd）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\paging_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\localPaging.mjs
 *   node tests\fix_verify\frontend\test_list_paging.mjs tests\fix_verify\frontend\.build\localPaging.mjs
 *   node tests\fix_verify\frontend\test_f12_page_bounds.mjs tests\fix_verify\frontend\.build\localPaging.mjs
 *
 * 也可用任意可写目录（如 %TEMP%\gipfel-fe-bundles）：先 cd 到该目录、-Entry 传绝对路径、
 * -Outfile 传文件名即可；tests\fix_verify\frontend\run_all.mjs 用的就是这种写法。
 */
import { applyLocalPaging } from "@/api/localPaging";

export { applyLocalPaging };
