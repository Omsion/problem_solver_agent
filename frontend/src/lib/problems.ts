import type { Task } from "../types";

/** 题型标签（后端使用英文枚举，界面展示中文） */
const KIND_LABELS: Record<string, string> = {
  MULTIPLE_CHOICE: "选择题",
  FILL_IN_THE_BLANKS: "填空题",
  CODING: "编程题",
  LEETCODE: "算法题",
  ACM: "算法题",
  ML_CODING: "机器学习编程题",
  VISUAL_REASONING: "图形推理题",
  QUESTION_ANSWERING: "问答题",
  GENERAL: "综合题",
};

export function problemKindLabel(problemType: string | null | undefined): string {
  if (!problemType) return "未识别题型";
  return KIND_LABELS[problemType] ?? problemType;
}

/** 任务卡片的标题：命名生成的文件名优先，否则用题型 */
export function taskKindLabel(task: Task): string {
  return problemKindLabel(task.problem_type);
}
