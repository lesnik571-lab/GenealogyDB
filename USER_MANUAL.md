# GenealogyDB User Manual

Version: 2.1.0-beta2-dev

## Getting started

GenealogyDB stores your database and related files in the GenealogyDB folder under your Windows Local AppData directory. The application creates its data, backups, exports, logs, and plugins folders on first start.

## Importing a GEDCOM file

Start the application, choose the GEDCOM import action, and select a `.ged` file. Review the import summary before opening the viewer.

## Finding people

Use the search fields at the top of the viewer to filter by name, dates, places, occupation, notes, GEDCOM ID, or database ID. Double-click a result to open its person card.

## People and relationships

Use the person editor to add or update a person. The relationship editor manages parents, spouses, and children. Undo and Redo are available from the Edit menu.

## Analysis tools

The viewer includes family tree, relationship path, timeline, life map, integrity report, data quality, recovery, and duplicate-management tools. These tools do not modify records unless an action explicitly asks for confirmation.

## Collaboration and project exchange

GenealogyDB 2.1 Beta supports collaboration identities, offline change packages, project merge previews, conflict-resolution plans, history browsing, and workflow automation. Preview and dry-run actions remain read-only. Completed writes use one shared operation identifier across audit, collaboration, and history records.

## Plugins and exports

Bundled plugins are copied to the user plugins folder on first start. Reports and exports contributed by plugins appear in the application menus. Exported files should be saved in the user exports folder or another writable location.

## Backups

Create a backup before large imports, merges, or repairs. Backups are stored outside the installed application and remain available when the application is upgraded or removed.
