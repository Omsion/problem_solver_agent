"""
file_monitor.py - 文件系统监控模块

使用 'watchdog' 监控截图目录，把新出现的图片交给回调处理。

设计要点：
1. 不再 `from .image_grouper import ImageGrouper`——监控模块不该反向依赖
   调度器，改为接收任意 callable，Web 端因此可以复用同一个监控器。
2. 新增文件稳定性检查：截图工具或浏览器写盘期间可能产生半截文件，
   直接送去 OCR 会得到残缺文本。这里等到文件大小稳定后再交付。
3. **补上改名就位（on_moved）**：Syncthing 等同步工具投递文件时是"先写
   `.syncthing.<名字>.tmp`、再改名成正式文件名"，Windows 上改名只会产生
   on_moved 事件。旧实现只处理 on_created，于是同步进来的照片躺在监控目录里
   从头到尾没人处理（2026-09-13 实测：文件 16:07 到位，几小时后仍在原地）。
4. **补偿扫描**：定时（默认 15s）与启动时各扫一遍目录，兜住任何丢失的事件
   （watchdog 事件缓冲区溢出、进程停机期间到达的文件）。配合 `SeenLedger`
   去重，保证同一张图片只投递一次。
5. **账本记的是"投递过"，不是"解出来了"**：若一次运行在流水线中途被强杀，
   图片已进账本而解答并不存在。因此启动时调用 `recover_stale_locks()` 清掉
   上一次进程残留的处理锁，且 `scan_once` **不再**把"锁存在"的文件记入账本 ——
   两者合起来保证被中断的图片能自动重投，而不是被永久跳过（2026-09-22 修复）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config
from .utils import setup_logger

logger = setup_logger()

# 文件稳定性判定参数。
# 注意：只比较"相邻两次采样"是不可靠的——活跃写入的文件在某个瞬间也可能
# 出现两次相同的大小（写入间隙），从而被误判为已写完。因此要求连续
# STABLE_ROUNDS 次采样大小都一致才放行，代价是引入 0.6s 的处理延迟
# （相比后续几十秒的识别求解可以忽略）。
STABILITY_CHECK_INTERVAL = 0.3
STABILITY_ROUNDS = 2
STABILITY_MAX_WAIT = 10.0

# 同步工具的临时/辅助文件名特征：都不是题目图片
_AUX_SUFFIXES = (".tmp", ".part", ".partial", ".crdownload")


def _is_aux_name(name: str) -> bool:
    """是不是同步工具/系统的辅助文件（临时文件、隐藏标记文件）。"""
    if name.startswith("."):  # .syncthing.x.jpg.tmp / .stfolder / .stignore
        return True
    return name.lower().endswith(_AUX_SUFFIXES)


def is_candidate_image(path: str | Path) -> bool:
    """是否是一张"应该送去解题"的图片。

    事件路径与补偿扫描共用这一判定，避免两条路径对"什么算图片"产生分歧。
    """
    candidate = Path(path)
    if _is_aux_name(candidate.name):
        return False
    return candidate.suffix.lower() in config.ALLOWED_EXTENSIONS


def wait_until_stable(
    path: Path,
    *,
    interval: float = STABILITY_CHECK_INTERVAL,
    rounds: int = STABILITY_ROUNDS,
    max_wait: float = STABILITY_MAX_WAIT,
) -> bool:
    """等到文件大小连续多次采样一致，才认为写盘完成。

    Args:
        interval: 两次采样之间的间隔（秒）
        rounds: 需要连续一致的采样次数（越大越保守）
        max_wait: 最长等待时间（秒），超时后仍返回 True 交给调用方处理

    Returns:
        文件是否可读（不存在则返回 False）
    """
    deadline = time.time() + max_wait
    previous: int | None = None
    stable = 0

    while time.time() < deadline:
        if not path.exists():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            time.sleep(interval)
            continue

        if previous is not None and size == previous and size > 0:
            stable += 1
            if stable >= rounds:
                return True
        else:
            stable = 0
        previous = size
        time.sleep(interval)

    logger.warning("等待文件稳定超时（%.1fs）：%s", max_wait, path.name)
    return path.exists()


# ---------------------------------------------------------------------------
# 去重账本
# ---------------------------------------------------------------------------


def _default_ledger_path() -> Path:
    """账本默认位置：解答目录。

    **绝不能放在监控目录里**——那通常是一个 Syncthing receive-only 文件夹，
    写入本地文件会触发"本地更改/需要覆盖"的提示，把同步搞乱。
    """
    return config.SOLUTION_DIR / ".monitor_seen.json"


class SeenLedger:
    """记录"已经投递过"的文件，避免补偿扫描反复投递同一张图。

    键为 `文件名|大小|mtime_ns`：同一个文件名被重新拍/重新同步（内容变了）
    仍会被当作新文件投递，这是期望行为。
    """

    def __init__(self, path: Path | None = None, *, limit: int = 1000) -> None:
        self.path = Path(path) if path is not None else None
        self.limit = limit
        self._entries: dict[str, float] = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._load()

    # -- 键计算 ---------------------------------------------------------
    @staticmethod
    def key(path: str | Path) -> str | None:
        candidate = Path(path)
        try:
            stat = candidate.stat()
        except OSError:
            return None
        return f"{candidate.name}|{stat.st_size}|{stat.st_mtime_ns}"

    # -- 查询/写入 ------------------------------------------------------
    def seen(self, path: str | Path) -> bool:
        key = self.key(path)
        if key is None:
            return False
        with self._lock:
            return key in self._entries

    def mark(self, path: str | Path) -> None:
        key = self.key(path)
        if key is None:
            return
        with self._lock:
            self._entries[key] = time.time()
            if len(self._entries) > self.limit:
                # 只保留最近的 limit 条，避免账本无限膨胀
                for old_key, _ in sorted(self._entries.items(), key=lambda kv: kv[1])[
                    : len(self._entries) - self.limit
                ]:
                    self._entries.pop(old_key, None)
            self._dirty = True

    def flush(self) -> None:
        """把账本原子写盘（内存模式 path=None 时什么都不做）。

        写盘时**每个文件名一行**（`indent` + 逐项排版）：账本是给人排查用的，
        一整行几百个键值对没法读。
        """
        if self.path is None:
            return
        with self._lock:
            if not self._dirty:
                return
            payload = dict(self._entries)
            self._dirty = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            # 一行一个文件名（键 = `文件名|大小|mtime_ns`，值 = 记账时间）。
            # 不用 `indent=2`：那会把每个键值的三个字段再拆成 4 行，反而更长；
            # 这里自定义排版，保证"一个文件 = 一行"，且键值本身保持紧凑。
            lines = ["{"]
            items = sorted(payload.items())
            for index, (key, stamp) in enumerate(items):
                comma = "," if index < len(items) - 1 else ""
                lines.append(f"  {json.dumps(key, ensure_ascii=False)}: {stamp}{comma}")
            lines.append("}")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning("写入监控去重账本失败（不影响本次处理）: %s", exc)

    def forget(self, names: Iterable[str] | None = None) -> list[str]:
        """把"已投递"记录清掉，让这些图片能被重新投递。

        为什么需要：账本记的是"投递过"而不是"解出来了"。一次运行如果在
        `_execute_pipeline` 里被强杀，图片已在账本里而解答并不存在 ——
        此时唯一的恢复手段就是把对应记录抹掉，让补偿扫描重新投递。

        Args:
            names: 要清除的**文件名**集合（如 `{"a.jpg"}`）；`None` 表示清空整个账本。

        Returns:
            实际被清除的文件名列表（去重、排序）。
        """
        wanted = None if names is None else {str(name) for name in names}
        removed: list[str] = []
        with self._lock:
            for key in list(self._entries):
                name = key.split("|", 1)[0]
                if wanted is None or name in wanted:
                    self._entries.pop(key, None)
                    removed.append(name)
            if removed:
                self._dirty = True
        if removed:
            self.flush()
        return sorted(set(removed))

    def names(self) -> list[str]:
        """账本里记录的全部文件名（去重、排序）。"""
        with self._lock:
            return sorted({key.split("|", 1)[0] for key in self._entries})

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("监控去重账本不可读，按空账本处理: %s", exc)
            return
        if isinstance(raw, dict):
            self._entries = {str(k): float(v) for k, v in raw.items()}


# ---------------------------------------------------------------------------
# 事件处理
# ---------------------------------------------------------------------------


class ImageEventHandler(FileSystemEventHandler):
    """把符合扩展名的图片交给回调；回调由调用方提供（CLI 传分组器，Web 传分组器）。

    `on_created`（直接写入）与 `on_moved`（改名就位，同步工具的行为）都走
    同一条投递路径。
    """

    def __init__(
        self,
        callback: Callable[[Path], None],
        *,
        wait_stable: bool = True,
        ledger: SeenLedger | None = None,
    ) -> None:
        self.callback = callback
        self.wait_stable = wait_stable
        self.ledger = ledger

    def on_created(self, event) -> None:
        self._handle(event, source="新建", path_attr="src_path")

    def on_moved(self, event) -> None:
        # 同步工具"临时文件 → 正式文件名"的那一步
        self._handle(event, source="改名就位", path_attr="dest_path")

    def _handle(self, event, *, source: str, path_attr: str) -> None:
        if getattr(event, "is_directory", False):
            return
        raw_path = getattr(event, path_attr, None)
        if not raw_path:
            return
        src_path = Path(raw_path)
        if not is_candidate_image(src_path):
            return

        logger.info("检测到新图片（%s）: %s", source, src_path.name)
        if self.wait_stable and not wait_until_stable(src_path):
            logger.warning("图片未就绪，忽略: %s", src_path.name)
            return
        if self.ledger is not None and self.ledger.seen(src_path):
            logger.info("该图片已投递过，跳过重复事件: %s", src_path.name)
            return
        try:
            self.callback(src_path)
        except Exception as exc:  # 回调异常不应杀死监控线程
            logger.error("处理新图片回调异常 %s: %s", src_path.name, exc, exc_info=True)
            return
        if self.ledger is not None:
            self.ledger.mark(src_path)


# ---------------------------------------------------------------------------
# 补偿扫描
# ---------------------------------------------------------------------------


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def group_by_mtime_gap(paths: Sequence[Path], gap_seconds: float) -> list[list[Path]]:
    """按 mtime 间隔分组：同一次连拍仍算一题，孤立的旧照片各自成题。

    补投时没有"事件时间"可用，只能用文件时间近似"同一个题目的连续截图"。
    """
    ordered = sorted(paths, key=_safe_mtime)
    groups: list[list[Path]] = []
    for path in ordered:
        if groups and _safe_mtime(path) - _safe_mtime(groups[-1][-1]) <= gap_seconds:
            groups[-1].append(path)
        else:
            groups.append([path])
    return groups


def scan_once(
    directory: str | Path,
    deliver_group: Callable[[Sequence[Path]], None],
    ledger: SeenLedger,
    *,
    max_age_minutes: int = 0,
    lock_dir: str | Path | None = None,
    wait_stable: bool = True,
) -> int:
    """扫一遍目录，把"没投递过"的图片按组补投。

    Returns:
        本次实际投递的图片数。
    """
    directory = Path(directory)
    try:
        candidates = [
            p for p in sorted(directory.iterdir()) if p.is_file() and is_candidate_image(p)
        ]
    except OSError as exc:
        logger.warning("补偿扫描无法读取目录 %s: %s", directory, exc)
        return 0

    now = time.time()
    fresh: list[Path] = []
    for path in candidates:
        if ledger.seen(path):
            continue
        # 正在被流水线处理（锁文件存在）：**跳过但绝不记账**。
        #
        # 这里曾经是 `ledger.mark(path)`（"记账以避免重复投递"），那是一个
        # 2026-09-22 定位到的数据丢失缺陷：锁文件只在 `ImageGrouper._execute_pipeline`
        # 的 `finally` 里删除，进程被强杀（Ctrl+C 之外、任务管理器结束、崩溃）时
        # `finally` 不执行 → 锁文件永久残留。旧逻辑此时把图片记成"已投递"，
        # 而它其实**从未被处理**；下次启动扫描看到"已投递"就永久跳过它 ——
        # 用户看到的就是满屏"该图片已投递过，跳过重复事件"却什么都解不出来。
        #
        # 新语义：锁只表示"本轮别投"，不表示"已完成"。锁残留时留着不记账，
        # 配合 `recover_stale_locks()`（启动时清掉上一次进程留下的锁）即可自动重投。
        if lock_dir is not None and (Path(lock_dir) / f".{path.stem}.lock").exists():
            continue
        if max_age_minutes and max_age_minutes > 0:
            age_minutes = (now - _safe_mtime(path)) / 60
            if age_minutes > max_age_minutes:
                # **只跳过，不记账** —— 与上面"锁"那条同一个道理（2026-09-23 修）。
                #
                # 这里曾经是 `ledger.mark(path)`，它制造了一个用户逃不出去的闭环：
                #   删掉账本想重投 → 下次启动扫描又被年龄闸门拦住 → 却又把"拦住"
                #   当成"已处理"记回账本 → 这些图此后永远不会再被自动评估。
                # 现场就是 16 张 6 天前的截图：账本删了又长回来，图一张没解。
                #
                # 年龄闸门要表达的是"启动时别把历史截图全重跑一遍"，那是**本轮**的
                # 决策，不是"这张图已完成"。不记账的代价只是每次启动多打印几行日志；
                # 好处是用户随时可以删账本（或调大 `MONITOR_CATCHUP_MAX_AGE_MINUTES`）
                # 让它们重新进入评估，而不需要额外的工具。
                logger.info(
                    "补偿扫描跳过过早的文件（%.0f 分钟前，未记账）：%s",
                    age_minutes, path.name,
                )
                continue
        fresh.append(path)

    if not fresh:
        ledger.flush()
        return 0

    delivered = 0
    for group in group_by_mtime_gap(fresh, config.GROUP_TIMEOUT):
        ready = [p for p in group if p.exists() and (not wait_stable or wait_until_stable(p))]
        if not ready:
            continue
        logger.info(
            "补偿扫描：补投 %d 张图片 → %s", len(ready), ", ".join(p.name for p in ready)
        )
        try:
            deliver_group(ready)
        except Exception as exc:
            logger.error("补偿扫描投递失败，下次扫描会重试: %s", exc, exc_info=True)
            continue
        for path in ready:
            ledger.mark(path)
        delivered += len(ready)

    ledger.flush()
    return delivered


# ---------------------------------------------------------------------------
# 启动监控
# ---------------------------------------------------------------------------

_WATCHDOG_LOGGING_ATTACHED = False


def _attach_watchdog_logging() -> None:
    """让 watchdog 自身的告警（如 Windows 事件缓冲区溢出）出现在我们的日志里。

    watchdog 用标准 logging，但项目没有配置 root logger，那些消息原本会**静默
    丢弃**——事件丢了却看不出任何迹象，正是漏检事故难以定位的原因之一。
    """
    global _WATCHDOG_LOGGING_ATTACHED
    if _WATCHDOG_LOGGING_ATTACHED:
        return
    wd_logger = logging.getLogger("watchdog")
    wd_logger.setLevel(logging.WARNING)
    for handler in logger.handlers:
        if handler not in wd_logger.handlers:
            wd_logger.addHandler(handler)
    _WATCHDOG_LOGGING_ATTACHED = True


def recover_stale_locks(lock_dir: str | Path) -> list[str]:
    """删除上一次进程残留的处理锁，返回被恢复的文件名列表。

    为什么需要：`ImageGrouper._execute_pipeline` 在 `finally` 里删锁，但**强杀**
    （任务管理器结束进程、断电、`Stop-Process -Force`）不执行 `finally` ——
    锁文件会永久留在 `SOLUTION_DIR` 下。后果有两条，都会让用户看到"图片被跳过"：

    1. 锁文件让 `scan_once` 认为"正在处理中"而跳过该图；
    2. 旧版 `scan_once` 还会顺手把它记进去重账本，于是**永久**跳过（已修，见该函数）。

    在**进程启动时**调用是安全的：此刻不可能有本进程发起的、仍在进行中的处理。
    唯一会误伤的场合是"同时跑两个实例"（第二个实例会把第一个正在用的锁删掉，
    导致同一组图被处理两次）—— 这种用法本身就会互相抢图，应先退出其中一个。

    Args:
        lock_dir: 锁文件所在目录（通常是 `config.SOLUTION_DIR`）。

    Returns:
        被删掉锁的文件名（不含 `.lock` 前缀），供调用方写日志或核对。
    """
    directory = Path(lock_dir)
    if not directory.exists():
        return []
    recovered: list[str] = []
    for lock_file in sorted(directory.glob(".*.lock")):
        try:
            lock_file.unlink()
            recovered.append(lock_file.name[1:-len(".lock")])
        except OSError as exc:
            logger.warning("清理残留锁文件失败 %s: %s", lock_file, exc)
    if recovered:
        logger.warning(
            "发现 %d 个上次运行残留的处理锁（任务被中断，锁未释放）：%s。"
            "这些图片会被重新处理。",
            len(recovered), ", ".join(recovered),
        )
    return recovered


def deliver_now(
    directory: str | Path,
    image_names: Iterable[str],
    deliver_group: Callable[[Sequence[Path]], None],
    ledger: SeenLedger,
    *,
    wait_stable: bool = False,
) -> list[Path]:
    """把指定的图片**立即**按 mtime 补投一次，绕开年龄闸门。

    为什么需要绕过 `max_age_minutes`：它存在的原因是"别在启动时把用户的历史截图全
    重跑一遍"（默认只补投 2 小时内到达的）。但**被中断的任务**恰恰往往更旧 ——
    2026-09-22 的事故里那批图是 6 天前拍的，清掉账本后仍会被年龄闸门挡下，用户
    重启多少次都等不到。用户显式点名要重投的图片，年龄不该是理由。

    与 `scan_once` 的分工：那个是**自动**补偿路径（保守，受年龄与锁的双重约束）；
    这个是**显式**重投路径（用户/工具明确指定文件，只做存在性与稳定性检查）。

    Args:
        directory: 图片所在目录。
        image_names: 要补投的文件名（相对 `directory`）。
        deliver_group: 整组投递回调（按 mtime 分组后调用）。
        ledger: 投递成功后逐张记账；失败的组不记账，便于下次重试。

    Returns:
        实际投递的图片路径（按投递顺序）。
    """
    directory = Path(directory)
    wanted: list[Path] = []
    for name in image_names:
        path = directory / name if not Path(name).is_absolute() else Path(name)
        if path.exists() and is_candidate_image(path):
            wanted.append(path)

    delivered: list[Path] = []
    for group in group_by_mtime_gap(wanted, config.GROUP_TIMEOUT):
        ready = [p for p in group if not wait_stable or wait_until_stable(p)]
        if not ready:
            continue
        try:
            deliver_group(ready)
        except Exception as exc:
            logger.error("补投失败（这些图片仍留在账本外，下次可重试）: %s", exc, exc_info=True)
            continue
        for path in ready:
            ledger.mark(path)
            delivered.append(path)
    ledger.flush()
    return delivered


class MonitorHandle:
    """监控句柄：封装 watchdog Observer 与补偿扫描线程。

    保持与 `Observer` 一致的 `is_alive()/stop()/join()` 接口，调用方无需关心
    内部多了一个补偿线程。
    """

    def __init__(
        self,
        observer: Observer,
        rescan_thread: threading.Thread | None,
        stop_flag: threading.Event,
    ) -> None:
        self.observer = observer
        self.rescan_thread = rescan_thread
        self._stop_flag = stop_flag

    def is_alive(self) -> bool:
        return self.observer.is_alive()

    def stop(self) -> None:
        self._stop_flag.set()
        self.observer.stop()

    def join(self, timeout: float | None = None) -> None:
        self.observer.join(timeout)
        if self.rescan_thread is not None:
            self.rescan_thread.join(timeout)


def _rescan_loop(
    directory: Path,
    deliver_group: Callable[[Sequence[Path]], None],
    ledger: SeenLedger,
    stop_flag: threading.Event,
    *,
    interval: float,
    max_age_minutes: int,
    lock_dir: Path | None,
) -> None:
    while not stop_flag.is_set():
        try:
            scan_once(
                directory,
                deliver_group,
                ledger,
                max_age_minutes=max_age_minutes,
                lock_dir=lock_dir,
            )
        except Exception as exc:  # 扫描异常绝不能杀死监控
            logger.error("补偿扫描异常: %s", exc, exc_info=True)
        if interval <= 0:
            return
        stop_flag.wait(interval)


def start_monitoring(
    path: Path,
    callback: Callable[[Path], None],
    *,
    wait_stable: bool = True,
    on_group: Callable[[Sequence[Path]], None] | None = None,
    ledger: SeenLedger | None = None,
) -> MonitorHandle:
    """启动目录监控（事件 + 补偿扫描）。

    Args:
        path: 需要监控的目录
        callback: 收到新图片时调用（接收图片路径）
        wait_stable: 是否等待文件写盘完成
        on_group: 补投一整组图片时的回调（通常是分组器的 `submit_group`）；
            缺省则退化为逐张调用 `callback`
        ledger: 去重账本，缺省用 `SOLUTION_DIR/.monitor_seen.json`

    Returns:
        MonitorHandle，调用方负责在退出时 stop/join
    """
    if ledger is None:
        ledger = SeenLedger(_default_ledger_path())

    handler = ImageEventHandler(callback, wait_stable=wait_stable, ledger=ledger)
    observer = Observer()
    observer.schedule(handler, str(path), recursive=False)
    observer.start()
    _attach_watchdog_logging()
    logger.info("文件监控已启动，正在监视目录: %s", path)

    deliver_group: Callable[[Sequence[Path]], None]

    def _deliver_each(group: Sequence[Path]) -> None:
        for image in group:
            callback(image)

    deliver_group = on_group or _deliver_each

    stop_flag = threading.Event()
    rescan_thread: threading.Thread | None = None
    if config.MONITOR_STARTUP_SCAN or config.MONITOR_RESCAN_INTERVAL > 0:
        rescan_thread = threading.Thread(
            target=_rescan_loop,
            args=(Path(path), deliver_group, ledger, stop_flag),
            kwargs={
                "interval": config.MONITOR_RESCAN_INTERVAL,
                "max_age_minutes": config.MONITOR_CATCHUP_MAX_AGE_MINUTES,
                "lock_dir": config.SOLUTION_DIR,
            },
            name="MonitorRescan",
            daemon=True,
        )
        rescan_thread.start()

    return MonitorHandle(observer, rescan_thread, stop_flag)
