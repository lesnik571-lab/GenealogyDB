import inspect

from viewer import GenealogyViewer


def test_main_toolbar_remains_accessible_at_high_dpi_without_opening_tkinter():
    source = inspect.getsource(GenealogyViewer._create_widgets)

    assert 'orient="horizontal"' in source
    assert "xscrollcommand=toolbar_scrollbar.set" in source
    assert "sync_toolbar_scrollregion" in source
    assert 'text="<"' in source
    assert 'text=">"' in source
    assert source.count(').pack(side="left"') >= 2
    assert "self._plugin_button_frame = top" in source


def test_primary_viewer_navigation_uses_consistent_russian_labels():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    for label in (
        'label="Правка"',
        'label="Справка"',
        'label="Инструменты"',
        'label="Анализ"',
        'label="Руководство пользователя"',
        'label="Центр релиза"',
        'label="Готовность Beta"',
        'label="Проверка RC1"',
        'label="Проверка интеграции 2.1"',
        'label="Диагностика"',
        'label="О программе"',
        'label="Совместная работа"',
        'label="Объединение проектов"',
        'label="Разрешение конфликтов"',
        'label="Просмотр истории"',
        'label="Автоматизация процессов"',
        'label="Автономный обмен изменениями"',
        'label="Центр анализа"',
        'label="Анализ источников"',
        'text="Резервная копия"',
        'text="Восстановить"',
        'text="Связи"',
        'text="Добавить человека"',
        'text="Изменить человека"',
        'text="Удалить человека"',
    ):
        assert label in source

    plugin_source = inspect.getsource(GenealogyViewer._register_plugin_menu_item)
    for label in ('"Отчёты"', '"Экспорт"', '"Плагины"'):
        assert label in plugin_source


def test_secondary_viewer_dialogs_use_russian_titles_and_actions():
    method_names = (
        "_show_about",
        "_show_user_manual",
        "open_intelligence_center",
        "open_source_analysis_center",
        "open_beta_readiness",
        "_record_beta_scaling",
        "open_rc1_validation",
        "open_21_integration_check",
        "open_collaboration",
        "open_project_merge",
        "open_conflict_resolution",
        "open_history_browser",
        "open_workflow_automation",
        "open_offline_change_exchange",
        "open_release_center",
        "open_release_notes",
        "open_performance_center",
        "_show_diagnostics",
    )
    source = "\n".join(
        inspect.getsource(getattr(GenealogyViewer, name))
        for name in method_names
    )
    for label in (
        "О GenealogyDB",
        "Руководство пользователя GenealogyDB",
        "Центр анализа",
        "Центр анализа источников",
        "Готовность Beta",
        "Проверка масштабирования",
        "Проверка RC1",
        "Проверка интеграции 2.1",
        "Совместная работа",
        "Объединение проектов",
        "Разрешение конфликтов",
        "Просмотр истории",
        "Автоматизация процессов",
        "Офлайн-обмен изменениями",
        "Центр релиза",
        "Примечания к выпуску",
        "Запустить тест",
        "Сохранить эталон",
        "Диагностика GenealogyDB",
    ):
        assert label in source

def test_source_analysis_statistics_are_presented_in_russian():
    statistics = {
        "total_sources": 3,
        "citations": 7,
        "average_citations_per_person": 1.5,
        "evidence_coverage": 80,
        "unsupported_records": 2,
        "duplicate_rate": 0,
    }

    text = GenealogyViewer._format_source_analysis_statistics(statistics)

    assert text == (
        "источники: 3; цитаты: 7; среднее на человека: 1.5; "
        "покрытие доказательствами: 80; без источников: 2; дубликаты: 0"
    )
    for technical_name in statistics:
        assert technical_name not in text

def test_rc_and_integration_validation_controls_are_russian():
    source = "\n".join(
        (
            inspect.getsource(GenealogyViewer.open_rc1_validation),
            inspect.getsource(GenealogyViewer.open_21_integration_check),
        )
    )

    assert source.count('text="Запустить проверку"') == 2
    assert "Проверка RC1 выполняется на временных базах данных" in source
    assert "Run validation" not in source
    assert "Run check" not in source

