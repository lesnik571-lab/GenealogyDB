# Changelog

## [2.2.0-beta2-dev] - 2026-08-05

### Changed
- Started Beta2 development after the verified and packaged `2.2.0-beta1` release.
- PyInstaller now writes intermediate build state to a disposable temporary directory, so release builds no longer dirty the tracked `build/GenealogyDB` files.
- Advanced development, build, installer, and user-manual version metadata to `2.2.0-beta2-dev`.

## [2.2.0-beta1] - 2026-08-05

### Added
- Added persistent recent-person navigation with per-database history, individual removal, clearing, and direct promotion to favorites or home person.
- Added a persistent per-database home person with quick open, set, clear, person-card actions, and `Ctrl+1` fallback navigation.
- Added live `⌂` home-person and `★` favorite markers to the main person list, Favorites, and Recent people.
- Added dedicated 2.2 Beta readiness validation for navigation sidecars, viewer contracts, packaging metadata, and configured-database safety.

### Changed
- Scoped favorite people by database while preserving migration from the legacy favorites format.
- Favorites and recent lists now prune missing people and refresh navigation state immediately after user actions.
- Synchronized application, build, installer, and user-manual version metadata to `2.2.0-beta1`.

### Validation
- 2.2 Beta readiness validation reports **READY FOR 2.2.0-BETA1** on disposable validation data without modifying the configured genealogy database.
- Verified 399 automated tests before the Beta1 version promotion.

## [2.2.0-dev] - 2026-08-04

### Added
- Added persistent favorite people for quick navigation from the Workspace menu and person card.
- Stored favorites in the user data directory without modifying the genealogy database.

### Changed
- Started the GenealogyDB 2.2 development cycle after the verified 2.1.0 final release.
- Updated development, build, installer, and user-manual version metadata to 2.2.0-dev.

## [2.1.0] - 2026-08-04

### Released
- Promoted the installed and verified 2.1.0 RC2 build to the final GenealogyDB 2.1.0 release.
- Preserved compatibility with existing GenealogyDB databases, backups, and user-side data directories.

### Validation
- Installed RC validation reports **READY FOR RC1** without modifying the configured database.
- Verified all 369 tests with warnings treated as errors.
- Verified the packaged application startup and Windows installer.

## [2.1.0-rc2] - 2026-08-04

### Fixed
- Made RC validation work from the installed PyInstaller executable without requiring source-only files.
- Validated embedded runtime resources and packaged version metadata separately from source-tree build definitions.
- Marked the source-only Viewer AST scan as not applicable inside the installed executable instead of blocking RC readiness.

### Validation
- Verified all 369 tests with warnings treated as errors.

## [2.1.0-rc1] - 2026-08-04

### Changed
- Started RC1 stabilization after the verified 2.1.0 Beta 2 release.
- Localized the RC1 validation report, status, categories, and validation controls in Russian.

### Fixed
- Allowed RC1 validation to run from the open Viewer without treating the existing Tkinter root as an import-time window leak.

### Validation
- RC1 workflow validation reports **READY FOR RC1** while preserving the configured database checksum.
- Verified all 368 tests with warnings treated as errors.

## [2.1.0-beta2] - 2026-08-04

### Fixed
- Added horizontal navigation to keep every main-toolbar action accessible at Windows high-DPI scaling.
- Closed SQLite initialization, audit, statistics, release-center, and test connections deterministically to avoid Python 3.14 finalization warnings.
- Prevented validation tests from overwriting tracked release reports or leaving the working tree dirty.
- Verified all 365 tests with warnings treated as errors.

### Changed
- Localized the Viewer menus, secondary commands, toolbar controls, dialog titles, actions, and source-analysis statistics consistently in Russian.

## [2.1.0-beta1] - 2026-08-04

### Added
- Collaboration identities and deterministic offline change-package exchange.
- Read-only project-merge previews, conflict-resolution plans, and history browsing.
- Workflow automation with dry-run, read-only, confirmation, cancellation, and backup boundaries.
- Shared operation UUID correlation across Audit, Collaboration, and History records.
- Beta validation reports for collaboration, exchange, merge, conflict resolution, workflows, packaging, and data safety.

### Changed
- Expanded the viewer with GenealogyDB 2.1 collaboration, research, visualization, automation, and release-center tools.
- Strengthened integration validation for package, merge, resolution, workflow, and history identities.

### Fixed
- Closed the Beta audit-consistency blocker by enforcing one correlated operation identity per logical write.
- Preserved compatibility for legacy records that do not contain the new correlation metadata.

## [2.0.0-beta1] - 2026-08-03

### Changed
- Beta readiness remediation and safe database-path diagnostics.

## [2.0] - 2026-07-31

### Added
- Modular GEDCOM parsing package under gedcom/
- Repository layer for database access and schema operations
- Relationship navigation for parents, spouses, children, and siblings
- Graphical family-tree view with zoom and drag/pan support
- Pytest-based automated regression tests for parsing, import, repositories, and viewer behavior

### Changed
- Refactored importer, database bootstrap, and viewer to rely on repository classes instead of direct SQL in the UI layer
- Preserved compatibility entry points in parser.py and app.py while improving the internal structure
- Updated application version to 2.0

### Fixed
- Improved person details navigation for related individuals
- Stabilized family-tree node generation and viewer integration
