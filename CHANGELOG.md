# Changelog

## [2.1.0-rc1-dev] - Unreleased\n\n### Changed\n- Started RC1 stabilization after the verified 2.1.0 Beta 2 release.\n\n## [2.1.0-beta2] - 2026-08-04

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
