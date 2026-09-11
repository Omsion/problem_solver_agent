// ---- SSE Events ----
export type SSEEventType =
  | "init"
  | "status"
  | "reasoning"
  | "chunk"
  | "timings"
  | "done"
  | "error"
  | "cancelled"
  | "verified"
  | "auto_imported"
  | "remote_connected"
  | "remote_disconnected";

/** 后端抽取出的「最终答案」 */
export interface AnswerCard {
  text: string;
  /** 是否命中了明确的「最终答案」小节（否则是降级取的首段） */
  extracted: boolean;
  section?: string | null;
  truncated?: boolean;
}

/** 核对模式的结论 */
export type VerifyVerdict = "agree" | "disagree" | "unclear";

export interface VerificationResult {
  verdict: VerifyVerdict;
  issues: string[];
  corrections: string;
  reason?: string;
  model?: string;
}

export interface SSEEvent {
  type: SSEEventType;
  task_id?: string;
  num_images?: number;
  phase?: ProgressPhase;
  message?: string;
  content?: string;
  filename?: string;
  source?: string; // for auto_imported: "monitor"
  timings?: StageTimings;
  client_ip?: string;
  answer_card?: AnswerCard;
  verification?: VerificationResult;
  resolved?: boolean;
}

// ---- Task (from REST API) ----
export type TaskStatus = "pending" | "processing" | "completed" | "failed" | "cancelled";

export interface Task {
  id: string;
  status: TaskStatus;
  problem_type: string | null;
  solver_provider: string | null;
  solver_model: string | null;
  filename: string | null;
  solution_path: string | null;
  error_message: string | null;
  num_images: number;
  created_at: number;
  updated_at: number;
  /** 阶段耗时（毫秒），后端迁移后提供 */
  timings?: StageTimings | null;
}

// ---- Upload File ----
export interface UploadFile {
  id: string;
  file: File;
  previewUrl: string;
}

// ---- Pipeline progress ----
export type ProgressPhase =
  | "idle"
  | "classifying"
  | "ocr"
  | "solving"
  | "verifying"
  | "archiving"
  | "done"
  | "cancelled"
  | "error";

/** 每个流水线阶段的实测耗时（毫秒）与缓存命中情况 */
export interface StageTimings {
  classify?: number;
  ocr?: number;
  polish?: number;
  solve?: number;
  total?: number;
  /** 哪些阶段命中了缓存（重试时复用） */
  cached?: string[];
}

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed" | "failed";

export interface ProgressState {
  phase: ProgressPhase;
  message: string;
  thinking: string; // accumulated reasoning content
  answer: string; // accumulated answer content
  filename: string | null;
  error: string | null;
  timings: StageTimings | null;
  streamStatus: StreamStatus;
  reconnectAttempt: number;
  /** 后端抽取的最终答案，用于「答案卡」展示 */
  answerCard: AnswerCard | null;
  /** 核对结果（用户主动触发） */
  verification: VerificationResult | null;
  /** 核对进行中 */
  verifying: boolean;
  /** 原标题文本（换路重解时展示） */
  resolved?: boolean;
  /**
   * 本次运行的真实开始时间（毫秒时间戳），用于「已用时」计时。
   *
   * 来自任务的 `created_at`（后端为秒级，前端 ×1000），换路重解/重试时重置为当前时间。
   * 缺失时前端退化为「第一次观察到它在运行」的时刻。
   */
  startedAt?: number | null;
}

// ---- API payloads ----
export interface TaskDetail {
  task: Task;
  solution_content: string;
  image_urls: string[];
  /**
   * 后端抽取的答案卡（与 SSE `done` 事件同源）。
   *
   * 有它就不要在前端自己"猜答案"：任务详情返回的是整个解答文件，
   * 直接取首段会把 YAML frontmatter / 题面当成最终答案。
   */
  answer_card?: AnswerCard | null;
}

export interface SystemStatus {
  auto_import_enabled: boolean;
  running: boolean;
  monitor_dir: string;
  group_timeout?: number;
  started_at?: number | null;
  last_group_at?: number | null;
  groups_handled?: number;
  processing?: number;
  uploads_bytes?: number;
  solutions_bytes?: number;
  remote_connected?: boolean;
  lan_ip?: string;
}

/** 后端 /api/health 的返回 */
export interface HealthInfo {
  status: string;
  version: string;
  vision_configured: boolean;
  solver_providers: string[];
  keys_configured: Record<string, boolean>;
}

/** 单个阶段的统计值 */
export interface StageStats {
  p50: number | null;
  p90: number | null;
  average: number | null;
  samples: number;
  cache_hits: number;
}

/** 后端 /api/stats 的返回 */
export interface StatsResponse {
  sample_size: number;
  completed: number;
  failed: number;
  cache_hit_rate: number;
  stages: Record<string, StageStats>;
}

// ---- 认证与用量（/api/v1）----

export type UserRole = "user" | "admin";

/**
 * 用户安全视图（后端 `User.to_public_dict()`）。
 *
 * 注意 `phone` 与 `api_key_masked` 都已经是掩码值：后端不会把真实手机号
 * 和完整密钥下发到前端，所以这里没有"未掩码"的对应字段可用。
 */
export interface AuthUser {
  id: string;
  phone: string;
  role: UserRole;
  tenant_id: string;
  budget: number;
  spent: number;
  remaining: number;
  api_key_masked: string;
  created_at: number;
  last_login_at: number | null;
}

/** 按模型的用量聚合 */
export interface UsageByModel {
  model: string;
  calls: number;
  cost: number;
}

/** 按流水线阶段的用量聚合 */
export interface UsageByStage {
  stage: string;
  calls: number;
  cost: number;
}

/** 用量汇总（后端 `AccountManager.usage_summary()`） */
export interface UsageSummary {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  by_model: UsageByModel[];
  by_stage: UsageByStage[];
}

/** 一条用量流水（管理员查看单个用户时返回） */
export interface UsageEvent {
  id: string;
  user_id: string;
  tenant_id: string;
  task_id: string | null;
  provider: string;
  model: string;
  stage: string;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  created_at: number;
}

/** `POST /auth/send-code` 的返回；`debug_code` 仅在开发模式（SMS_PROVIDER=console）出现 */
export interface SendCodeResponse {
  ok: boolean;
  provider: string;
  expires_in: number;
  debug_code?: string;
  message?: string;
}

/** 注册 / 登录成功后的令牌载荷 */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
  user: AuthUser;
}

/** `GET /auth/me` 的返回 */
export interface MeResponse {
  user: AuthUser;
  usage: UsageSummary;
  auth_enabled: boolean;
}

/** `GET /admin/dashboard` 的返回 */
export interface AdminDashboard {
  total_users: number;
  calls: number;
  cost: number;
  input_tokens: number;
  output_tokens: number;
  /** 按花费倒序的前 20 名用户，附带全局口径的 cost/calls */
  top_users: AdminTopUser[];
}

export interface AdminTopUser {
  id: string;
  phone: string;
  role: UserRole;
  budget: number;
  spent: number;
  cost: number;
  calls: number;
}

/** `GET /admin/users` 的返回（与 react-admin 的 simpleRestProvider 约定一致） */
export interface AdminUserList {
  data: AuthUser[];
  total: number;
}

/** `GET /admin/users/{id}` 的返回 */
export interface AdminUserDetail {
  user: AuthUser;
  usage: UsageSummary;
  events: UsageEvent[];
}
