# Changelog

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
