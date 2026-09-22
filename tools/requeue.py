# -*- coding: utf-8 -*-
"""requeue.py - 把"投递过但没解出来"的图片重新排队

## 什么时候需要它

监控器的去重账本（`<SOLUTION_DIR>/.monitor_seen.json`）记录的是**"投递过"**，
不是**"解出来了"**。一次运行如果在流水线中途被强杀（任务管理器结束进程、崩溃、
终端被关掉），图片已经进账本、解答却不存在 —— 之后每一次启动都会看到

    检测到新图片（新建）: xxx.jpg
    该图片已投递过，跳过重复事件: xxx.jpg

而目录里什么都不会发生。这是 2026-09-22 定位到的真实问题，根因与修复见
`problem_solver_agent/file_monitor.py` 的 `recover_stale_locks()` 与 `scan_once()`。

**还有第二道闸门**：`MONITOR_CATCHUP_MAX_AGE_MINUTES`（默认 120）会让启动扫描
跳过"太旧"的图片，而被中断的那批图往往已经放了好几天 —— 所以光清账本不够，
本工具默认直接用 `deliver_now()` 把它们投进流水线，不等下一次扫描。

## 用法

    # 1) 先看会重投什么（不写盘、不调用 API）
    py -3.10 -m tools.requeue --dry-run

    # 2) 重投某一批（按文件名通配）
    py -3.10 -m tools.requeue --pattern "IMG_20260916_19*.jpg"

    # 3) 只清账本、不立即投递（下次启动由扫描补投，受年龄闸门约束）
    py -3.10 -m tools.requeue --all --no-deliver

    # 4) 清空账本并立即重投监控目录里所有图片
    py -3.10 -m tools.requeue --all

投递走的是与监控完全相同的回调（`ImageGrouper.add_image` + `submit_group`），
分组规则与正常运行时一致（同一组按 mtime 间隔切分）。
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from problem_solver_agent import config  # noqa: E402
from problem_solver_agent.file_monitor import (  # noqa: E402
    SeenLedger,
    deliver_now,
    recover_stale_locks,
)


def _ledger_path() -> Path:
    return Path(config.SOLUTION_DIR) / ".monitor_seen.json"


def _targets(ledger: SeenLedger, monitor_dir: Path, pattern: str | None) -> list[str]:
    """挑出要重投的图片。

    默认以**监控目录里现在仍然存在的图片**为准（而不是账本条目）—— 账本里指向已删
    文件的记录（早被归档走的那批）不需要重投，清了也无意义。
    """
    names = []
    for path in sorted(monitor_dir.iterdir()) if monitor_dir.exists() else []:
        if not path.is_file():
            continue
        if pattern and not fnmatch.fnmatch(path.name, pattern):
            continue
        names.append(path.name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pattern", help="只处理匹配该通配符的文件名（如 'IMG_20260916_19*.jpg'）")
    parser.add_argument("--all", action="store_true", help="处理监控目录里的全部图片（默认行为）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘、不投递")
    parser.add_argument("--no-deliver", action="store_true",
                        help="只清账本，不立即投递（交给下次启动扫描，受年龄闸门约束）")
    parser.add_argument("--keep-locks", action="store_true", help="不要清理残留的处理锁")
    args = parser.parse_args()

    solution_dir = Path(config.SOLUTION_DIR)
    monitor_dir = Path(config.MONITOR_DIR)
    ledger = SeenLedger(_ledger_path())

    targets = _targets(ledger, monitor_dir, args.pattern)
    # 只对"账本里记着已投递"的图片动手，避免把从没投过的新图重复计一遍
    observed = set(ledger.names())

    print(f"账本      : {_ledger_path()}（{len(observed)} 条）")
    print(f"监控目录  : {monitor_dir}")
    print(f"解答目录  : {solution_dir}")
    print(f"命中图片  : {len(targets)}")
    for name in targets:
        flag = "已投递过" if name in observed else "未投递过"
        print(f"  - {name}  [{flag}]")

    locks = sorted(solution_dir.glob(".*.lock")) if solution_dir.exists() else []
    if locks and not args.keep_locks:
        print(f"\n残留处理锁 {len(locks)} 个：")
        for lock in locks:
            print(f"  - {lock.name}")

    if args.dry_run:
        print("\n（--dry-run：未做任何改动）")
        return 0

    if not targets:
        print("\n没有命中的图片。")
        return 0

    # 顺序很重要：先清账本与锁，再投递。否则投递成功的图片会被 deliver_now 重新记账，
    # 而"清账本"这一步如果排在后面，会把刚记好的完成状态又抹掉。
    removed = ledger.forget(targets)
    if removed:
        print(f"\n已清除 {len(removed)} 条账本记录。")
    if locks and not args.keep_locks:
        recovered = recover_stale_locks(solution_dir)
        print(f"已清理 {len(recovered)} 个残留锁。")

    if args.no_deliver:
        print(
            "\n--no-deliver：不立即投递。重启监控后由启动扫描补投"
            f"（注意 MONITOR_CATCHUP_MAX_AGE_MINUTES={config.MONITOR_CATCHUP_MAX_AGE_MINUTES} "
            "会跳过更旧的文件）。"
        )
        return 0

    # 立即投递：复用与监控完全相同的回调，保证分组行为一致
    from problem_solver_agent.image_grouper import ImageGrouper

    grouper = ImageGrouper()
    print(f"\n开始投递 {len(targets)} 张图片（按 mtime 分组，组内间隔 ≤{config.GROUP_TIMEOUT}s）…")
    delivered = deliver_now(monitor_dir, targets, grouper.submit_group, ledger)
    print(f"已投递 {len(delivered)} 张，交给 {len(grouper.workers)} 个处理线程。")

    # 工作线程是 daemon，主线程一退出就被杀掉 —— 必须等队列排空，
    # 否则会在流水线中途"取消"，留下 `.partial.md` 与一个残留锁。
    grouper.task_queue.join()
    print("全部处理结束。解答文件见:", solution_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

