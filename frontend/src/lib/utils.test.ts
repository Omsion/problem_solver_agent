import { describe, expect, it } from "vitest";
import { formatDuration, formatTs } from "./utils";
import { isActiveStatus, isTerminalStatus, statusLabel } from "./taskStatus";
import { problemKindLabel, taskKindLabel } from "./problems";

describe("formatTs（缺陷 D 回归）", () => {
  it("正常时间戳按本地格式输出", () => {
    const text = formatTs(1781577269.86);
    expect(text).not.toBe("--");
    expect(text.length).toBeGreaterThan(8);
  });

  it("非法输入返回占位符而不是抛错", () => {
    // 旧实现直接 new Date(ts*1000).toLocaleString()，遇到 NaN 会抛
    // RangeError: Invalid time value，React 会卸载整棵子树 → 手机端整页空白
    expect(() => formatTs(NaN)).not.toThrow();
    expect(formatTs(NaN)).toBe("--");
    expect(formatTs(0)).toBe("--");
    expect(formatTs(-1)).toBe("--");
    expect(formatTs(undefined)).toBe("--");
    expect(formatTs(null)).toBe("--");
  });

  it("Infinity 也被拦下", () => {
    expect(formatTs(Infinity)).toBe("--");
  });
});

describe("formatDuration", () => {
  it("按量级选择单位", () => {
    expect(formatDuration(820)).toBe("820ms");
    expect(formatDuration(4200)).toBe("4.2s");
    expect(formatDuration(72000)).toBe("1m12s");
  });

  it("非法输入返回占位符", () => {
    expect(formatDuration(NaN)).toBe("--");
    expect(formatDuration(undefined)).toBe("--");
    expect(formatDuration(-5)).toBe("--");
  });

  it("0 毫秒是合法值", () => {
    expect(formatDuration(0)).toBe("0ms");
  });
});

describe("任务状态判断", () => {
  it("终态包含成功、失败与取消", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
    expect(isTerminalStatus("cancelled")).toBe(true);
    expect(isTerminalStatus("processing")).toBe(false);
    expect(isTerminalStatus(undefined)).toBe(false);
  });

  it("进行中包含 pending 与 processing", () => {
    expect(isActiveStatus("pending")).toBe(true);
    expect(isActiveStatus("processing")).toBe(true);
    expect(isActiveStatus("cancelled")).toBe(false);
  });

  it("取消状态有独立文案，不再与失败混淆", () => {
    expect(statusLabel("cancelled")).toBe("已取消");
    expect(statusLabel("failed")).toBe("失败");
    expect(statusLabel(undefined)).toBe("未知");
    expect(statusLabel("weird")).toBe("weird");
  });
});

describe("题型标签", () => {
  it("已知枚举转中文", () => {
    expect(problemKindLabel("MULTIPLE_CHOICE")).toBe("选择题");
    expect(problemKindLabel("FILL_IN_THE_BLANKS")).toBe("填空题");
    expect(problemKindLabel("VISUAL_REASONING")).toBe("图形推理题");
  });

  it("未知与缺失有兜底", () => {
    expect(problemKindLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(problemKindLabel(null)).toBe("未识别题型");
  });

  it("任务卡片标题优先用题型", () => {
    expect(
      taskKindLabel({
        id: "t",
        status: "completed",
        problem_type: "CODING",
        solver_provider: null,
        solver_model: null,
        filename: "x.md",
        solution_path: null,
        error_message: null,
        num_images: 1,
        created_at: 1,
        updated_at: 1,
      }),
    ).toBe("编程题");
  });
});
