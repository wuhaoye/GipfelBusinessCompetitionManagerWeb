/**
 * 级联删除确认文案用例入口：真实 frontend/src/utils/deleteConfirmMessage.ts。
 *
 * 供 test_delete_confirm_message.mjs（F-02：存储型 XSS 转义）使用。
 *
 * 复现命令（仓库根目录）：
 *   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fix_verify\frontend\bundle.ps1 `
 *     -Entry tests\fix_verify\frontend\entries\delete_confirm_entry.mjs `
 *     -Outfile tests\fix_verify\frontend\.build\deleteConfirmMessage.mjs
 *   node tests\fix_verify\frontend\test_delete_confirm_message.mjs tests\fix_verify\frontend\.build\deleteConfirmMessage.mjs
 */
import { buildDeleteConfirmMessage, escapeHtml } from "@/utils/deleteConfirmMessage";

export { buildDeleteConfirmMessage, escapeHtml };
