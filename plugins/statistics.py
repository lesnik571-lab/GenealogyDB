import csv


plugin_name = "Statistics"
plugin_version = "1.0.0"


def register(app):
    """Register the built-in statistics report with the host app."""
    def statistics_report():
        return app.data.statistics()

    def export_statistics(destination):
        statistics = statistics_report()
        with destination.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["metric", "count"])
            writer.writerows(statistics.items())

    open_report = app.add_read_only_report(plugin_name, statistics_report)
    app.add_export("Statistics CSV", export_statistics)
    app.add_viewer_button(plugin_name, open_report)
    app.add_menu_item("Plugins", plugin_name, open_report)