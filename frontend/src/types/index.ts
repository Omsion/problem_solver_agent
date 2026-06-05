// ---- SSE Events ----
export interface SSEEvent {
  type: "init" | "status" | "reasoning" | "chunk" | "done" | "error";
  task_id?: string;
  num_images?: number;
  phase?: string;
  message?: string;
  content?: string;
  filename?: string;
}

// ---- Task (from REST API) ----
export interface Task {
  id: string;
  status: "pending" | "processing" | "completed" | "failed";
  problem_type: string | null;
  solver_provider: string | null;
  solver_model: string | null;
  filename: string | null;
  solution_path: string | null;
  error_message: string | null;
  num_images: number;
  created_at: number;
  updated_at: number;
}

// ---- Upload File ----
export interface UploadFile {
  id: string;
  file: File;
  previewUrl: string;
}

// ---- Pipeline Progress ----
export interface ProgressState {
  phase: "idle" | "classifying" | "ocr" | "solving" | "done" | "error";
  message: string;
  thinking: string;       // accumulated reasoning content
  answer: string;         // accumulated answer content
  filename: string | null;
  error: string | null;
}
