import threading
import time
import inspect

import pytest

from database import initialize_database
from performance_service import BoundedLRUCache, PerformanceService
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


def test_timer_exception_nested_percentiles_and_concurrent_recording(tmp_path):
    service = PerformanceService(tmp_path)
    with service.timer("outer", "viewer", records_processed=2):
        with service.timer("inner", "viewer", records_processed=1):
            pass
    with pytest.raises(ValueError):
        with service.timer("failed", "viewer"):
            raise ValueError("expected")
    threads = [threading.Thread(target=lambda: [service.record("parallel", "tests", 0.01) for _ in range(20)]) for _ in range(4)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()

    summaries = {(row["operation"], row["module"]): row for row in service.summaries()}
    assert summaries[("failed", "viewer")]["failure_count"] == 1
    assert summaries[("parallel", "tests")]["invocation_count"] == 80
    assert PerformanceService.percentile([1, 2, 3, 4], 50) == 2.5


def test_retention_cache_exports_baseline_and_synthetic_benchmark_isolation(tmp_path):
    service = PerformanceService(tmp_path, max_files=1, max_age_days=1, max_total_bytes=100000)
    service.record("search", "repository", 0.01, 10)
    service.persist(); time.sleep(0.002); service.persist()
    assert len(list((tmp_path / "performance").glob("metrics-*.json"))) == 1
    assert PerformanceService(tmp_path).summaries()
    cache = BoundedLRUCache(2); cache.set("a", 1); cache.set("b", 2); assert cache.get("a") == 1; cache.set("c", 3)
    assert cache.get("b") is None and cache.stats()["hits"] == 1
    cache.invalidate("a"); assert cache.get("a") is None
    baseline = service.save_baseline(); service.record("search", "repository", 1.0, 10)
    assert service.compare_baseline(baseline, regression_percent=1)
    assert service.export_csv(tmp_path / "metrics.csv").exists()
    assert service.export_json(tmp_path / "metrics.json").exists()
    result = service.run_benchmark("person_search", 1000)
    assert result["records_processed"] > 0 and not (tmp_path / "benchmark.sqlite").exists()


def test_pagination_ordering_cancellation_and_diagnostics_do_not_change_data(tmp_path):
    database_path = tmp_path / "family.db"; initialize_database(database_path)
    repository = PersonRepository(database_path)
    try:
        for index in range(5):
            repository.create_person({"gedcom_id": f"I{index}", "first_name": f"P{index}", "last_name": "Doe"})
        before = repository.capture_command_state()
        page, total = repository.list_people_page(surname="Doe", limit=2, offset=2, include_total=True)
        assert total == 5 and [row[0] for row in page] == sorted(row[0] for row in page)
        service = PerformanceService(tmp_path)
        stop = service.start_timer("cancelled", "tests")
        stop(cancelled=True)
        with pytest.raises(RuntimeError):
            service.run_benchmark("pagination", 1000, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        assert repository.capture_command_state() == before
        assert next(row for row in service.summaries() if row["operation"] == "cancelled")["cancellation_count"] == 1
    finally:
        repository.close()


def test_performance_menu_is_registered_without_opening_tkinter():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    assert 'label="Диагностика"' in source
    assert 'label="Производительность"' in source
