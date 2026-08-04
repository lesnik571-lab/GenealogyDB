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
