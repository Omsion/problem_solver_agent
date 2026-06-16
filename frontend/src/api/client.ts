/**
 * 兼容层：历史代码从 `api/client` 导入，实现已迁移到 `lib/api`。
 * 新代码请直接从 `../lib/api` 导入。
 */
export {
  ApiError,
  createTask,
  getTask,
  listTasks,
  deleteTask,
  cancelTask,
  retryTask,
  sseUrl,
  globalSseUrl,
  getSystemStatus,
} from "../lib/api";
