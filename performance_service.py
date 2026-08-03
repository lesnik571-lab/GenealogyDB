"""Read-only performance diagnostics, bounded caches, and synthetic benchmarks."""

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import sqlite3
import statistics
import threading
import time
from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterable, Mapping

from config import DATA_DIR


@dataclass(frozen=True)
class MetricSample:
    operation: str
    module: str
    duration_seconds: float
    records_processed: int
    cancelled: bool
    failed: bool
    timestamp: str


class BoundedLRUCache:
    """Small thread-safe LRU cache with explicit hit/miss accounting."""

    def __init__(self, capacity: int = 256) -> None:
        self.capacity = max(1, int(capacity))
        self._values: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: Any, default: Any = None) -> Any:
        with self._lock:
            if key not in self._values:
                self.misses += 1
                return default
            self.hits += 1
            self._values.move_to_end(key)
            return self._values[key]

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self.capacity:
                self._values.popitem(last=False)

    def invalidate(self, key: Any | None = None) -> None:
        with self._lock:
            if key is None:
                self._values.clear()
            else:
                self._values.pop(key, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"size": len(self._values), "capacity": self.capacity, "hits": self.hits, "misses": self.misses}


class PerformanceService:
    """Collect metrics in memory and persist only diagnostic sidecars."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        max_files: int = 30,
        max_age_days: int = 30,
        max_total_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        self.data_dir = Path(data_dir or DATA_DIR) / "performance"
        self.max_files = max(1, int(max_files))
        self.max_age_days = max(1, int(max_age_days))
        self.max_total_bytes = max(1, int(max_total_bytes))
        self._samples: list[MetricSample] = []
        self._lock = threading.RLock()
        self._local = threading.local()
        self.load_persisted()

    def load_persisted(self) -> None:
        """Best-effort recovery of diagnostic sidecars; malformed files are ignored."""
        if not self.data_dir.exists():
            return
        recovered = []
        for path in sorted(self.data_dir.glob("metrics-*.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
                recovered.extend(MetricSample(**row) for row in rows if isinstance(row, dict))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        with self._lock:
            self._samples.extend(recovered)

    @contextmanager
    def timer(self, operation: str, module: str, *, records_processed: int = 0):
        started = time.perf_counter()
        stack = getattr(self._local, "stack", [])
        self._local.stack = [*stack, (operation, module)]
        cancelled = failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            self._local.stack = stack
            self.record(operation, module, time.perf_counter() - started, records_processed, cancelled=cancelled, failed=failed)

    def decorator(self, operation: str, module: str, records: Callable[..., int] | None = None):
        def decorate(function):
            @wraps(function)
            def wrapped(*args, **kwargs):
                count = records(*args, **kwargs) if records else 0
                with self.timer(operation, module, records_processed=count):
                    return function(*args, **kwargs)
            return wrapped
        return decorate

    def start_timer(self, operation: str, module: str, *, records_processed: int = 0) -> Callable[[bool, bool, int | None], None]:
        started = time.perf_counter()
        completed = False
        def stop(cancelled: bool = False, failed: bool = False, records: int | None = None) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            self.record(operation, module, time.perf_counter() - started, records_processed if records is None else records, cancelled, failed)
        return stop

    def record(self, operation: str, module: str, duration_seconds: float, records_processed: int = 0, cancelled: bool = False, failed: bool = False) -> None:
        sample = MetricSample(operation, module, max(0.0, float(duration_seconds)), max(0, int(records_processed)), bool(cancelled), bool(failed), datetime.now(timezone.utc).isoformat())
        with self._lock:
            self._samples.append(sample)

    def summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            grouped: dict[tuple[str, str], list[MetricSample]] = defaultdict(list)
            for sample in self._samples:
                grouped[(sample.operation, sample.module)].append(sample)
        result = []
        for (operation, module), samples in sorted(grouped.items()):
            values = sorted(item.duration_seconds for item in samples)
            latest = samples[-1]
            total_seconds = sum(values)
            total_records = sum(item.records_processed for item in samples)
            result.append({"operation": operation, "module": module, "invocation_count": len(samples), "latest_duration": latest.duration_seconds, "average_duration": statistics.fmean(values), "minimum_duration": values[0], "maximum_duration": values[-1], "p50": self.percentile(values, 50), "p95": self.percentile(values, 95), "p99": self.percentile(values, 99), "records_processed": total_records, "throughput": total_records / total_seconds if total_seconds else 0.0, "cancellation_count": sum(item.cancelled for item in samples), "failure_count": sum(item.failed for item in samples), "last_run": latest.timestamp})
        return result

    @staticmethod
    def percentile(values: Iterable[float], percent: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        index = (len(ordered) - 1) * (float(percent) / 100)
        lower, upper = math.floor(index), math.ceil(index)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)

    def persist(self) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / f"metrics-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}.json"
        path.write_text(json.dumps([asdict(item) for item in self._samples], ensure_ascii=False, indent=2), encoding="utf-8")
        self.cleanup_retention()
        return path

    def cleanup_retention(self) -> None:
        if not self.data_dir.exists():
            return
        files = sorted((path for path in self.data_dir.glob("*.json") if path.is_file()), key=lambda item: item.stat().st_mtime, reverse=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
        total = 0
        for index, path in enumerate(files):
            stat = path.stat()
            old = datetime.fromtimestamp(stat.st_mtime, timezone.utc) < cutoff
            total += stat.st_size
            if old or index >= self.max_files or total > self.max_total_bytes:
                path.unlink(missing_ok=True)

    def clear_metrics(self) -> None:
        with self._lock:
            self._samples.clear()
        if self.data_dir.exists():
            for path in self.data_dir.glob("*.json"):
                path.unlink(missing_ok=True)

    def export_csv(self, destination: str | Path) -> Path:
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.summaries()
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["operation", "module"])
            writer.writeheader(); writer.writerows(rows)
        return path

    def export_json(self, destination: str | Path) -> Path:
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summaries(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_baseline(self, destination: str | Path | None = None) -> Path:
        path = Path(destination or self.data_dir / "baseline.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.summaries(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def compare_baseline(self, baseline: str | Path | None = None, *, regression_percent: float = 25.0) -> list[dict[str, Any]]:
        path = Path(baseline or self.data_dir / "baseline.json")
        if not path.exists():
            return []
        prior = {(row["operation"], row["module"]): row for row in json.loads(path.read_text(encoding="utf-8"))}
        changes = []
        for row in self.summaries():
            base = prior.get((row["operation"], row["module"]))
            if base and base["average_duration"] > 0:
                percent = (row["average_duration"] / base["average_duration"] - 1) * 100
                if percent > regression_percent:
                    changes.append({"operation": row["operation"], "module": row["module"], "regression_percent": percent})
        return changes

    def run_benchmark(self, name: str, size: int = 1000, cancel_callback: Callable[[], None] | None = None) -> dict[str, Any]:
        """Run deterministic synthetic diagnostics in a temporary database only."""
        size = max(1, min(int(size), 100000))
        with TemporaryDirectory(prefix="genealogydb-benchmark-") as directory:
            database_path = Path(directory) / "benchmark.sqlite"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, last_name TEXT, first_name TEXT)")
                connection.executemany("INSERT INTO people (id, last_name, first_name) VALUES (?, ?, ?)", ((index, f"Surname{index % 100}", f"Person{index}") for index in range(size)))
                connection.commit()
                if cancel_callback: cancel_callback()
                started = time.perf_counter()
                if name in {"person_search", "surname_filter", "pagination"}:
                    query = "SELECT id, last_name, first_name FROM people WHERE last_name = ? ORDER BY last_name, first_name, id LIMIT ? OFFSET ?"
                    rows = connection.execute(query, ("Surname1", min(100, size), 0)).fetchall()
                elif name == "timeline_ordering":
                    rows = sorted(((index % 2000, index) for index in range(size)))
                elif name == "map_clustering":
                    rows = {(index % 90, index % 180) for index in range(size)}
                elif name == "sidecar_json":
                    probe = Path(directory) / "sidecar.json"; probe.write_text(json.dumps(list(range(min(size, 5000)))), encoding="utf-8"); rows = json.loads(probe.read_text(encoding="utf-8"))
                else:
                    rows = connection.execute("SELECT COUNT(*) FROM people").fetchall()
                duration = time.perf_counter() - started
                count = len(rows) if hasattr(rows, "__len__") else size
                self.record(f"benchmark:{name}", "benchmarks", duration, count)
                return {"name": name, "size": size, "duration_seconds": duration, "records_processed": count, "throughput": count / duration if duration else 0.0, "database_path": str(database_path)}
            finally:
                connection.close()

    def run_quick_benchmarks(self, cancel_callback: Callable[[], None] | None = None) -> list[dict[str, Any]]:
        return [self.run_benchmark(name, 1000, cancel_callback) for name in ("person_search", "surname_filter", "pagination", "timeline_ordering", "map_clustering", "sidecar_json")]
