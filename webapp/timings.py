"""
timings.py - 阶段耗时统计

把每个任务的阶段耗时（毫秒）汇总成 P50/P90/均值，用于回答"到底慢在哪"。
纯函数实现，便于单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGES = ("classify", "ocr", "polish", "solve", "total")


@dataclass
class StageStat:
    """单个阶段的统计结果。"""

    samples: int = 0
    cache_hits: int = 0
    p50: float | None = None
    p90: float | None = None
    average: float | None = None


@dataclass
class TimingsStats:
    sample_size: int = 0
    completed: int = 0
    failed: int = 0
    stages: dict[str, StageStat] = field(default_factory=dict)
    cache_hit_rate: float = 0.0


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    """线性插值分位数。空列表返回 None。"""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _extract_timings(task: dict) -> dict | None:
    """从任务记录里取出耗时字典，兼容 JSON 字符串与已解析的 dict。"""
    raw = task.get("timings") or task.get("timings_json")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def aggregate_timings(tasks: list[dict]) -> TimingsStats:
    """把任务列表汇总为阶段耗时统计。

    - 只统计取值合法（正整数）的阶段
    - `cached` 列表里的阶段计入缓存命中
    - 无论是否有耗时数据，都会统计完成/失败数量
    """
    collections: dict[str, list[float]] = {name: [] for name in STAGES}
    cache_hits: dict[str, int] = {name: 0 for name in STAGES}
    completed = 0
    failed = 0

    for task in tasks:
        status = task.get("status")
        if status == "completed":
            completed += 1
        elif status in ("failed", "cancelled"):
            failed += 1

        timings = _extract_timings(task)
        if not timings:
            continue

        cached = timings.get("cached") or []
        if isinstance(cached, list):
            for name in cached:
                if name in cache_hits:
                    cache_hits[name] += 1

        for name in STAGES:
            value = timings.get(name)
            if isinstance(value, (int, float)) and value > 0:
                collections[name].append(float(value))

    stages: dict[str, StageStat] = {}
    for name, values in collections.items():
        if not values:
            stages[name] = StageStat(samples=0, cache_hits=cache_hits[name])
            continue
        ordered = sorted(values)
        stages[name] = StageStat(
            samples=len(ordered),
            cache_hits=cache_hits[name],
            p50=_percentile(ordered, 0.5),
            p90=_percentile(ordered, 0.9),
            average=sum(ordered) / len(ordered),
        )

    total_stage = stages.get("total")
    total_samples = total_stage.samples if total_stage else 0
    total_hits = sum(cache_hits.values())
    rate = (total_hits / total_samples) if total_samples else 0.0

    return TimingsStats(
        sample_size=len(tasks),
        completed=completed,
        failed=failed,
        stages=stages,
        cache_hit_rate=rate,
    )
