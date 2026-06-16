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
  | "auto_imported"
  | "remote_connected"
  | "remote_disconnected";

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
}

// ---- API payloads ----
export interface TaskDetail {
  task: Task;
  solution_content: string;
  image_urls: string[];
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
