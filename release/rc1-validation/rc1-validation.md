# GenealogyDB RC1 Validation

Recommendation: **READY FOR RC1 WITH WARNINGS**
Version: `2.0.0-beta1`
Configured database checksum: `missing` -> `missing`

| ID | Category | Status | Duration | Evidence | Reason | Cleanup |
| --- | --- | --- | ---: | --- | --- | --- |
| database.configured-availability | Database | WARNING | 0.000s | C:\Users\lesni\AppData\Local\Temp\pytest-of-lesnik\pytest-525\test_missing_configured_databa0\missing.db | Configured database does not exist; validation did not create it. | not applicable |
| startup.headless-import | Startup | WARNING | 0.000s | tk._default_root remained unset | cleanup failed: locked | cleanup failed: locked |
| startup.no-background-task | Startup | WARNING | 0.000s | new thread ids=[] | cleanup failed: locked | cleanup failed: locked |
| startup.viewer-safety | Startup | WARNING | 0.219s | static AST scan | cleanup failed: locked | cleanup failed: locked |
| database.first-startup | Startup | WARNING | 0.000s | temporary database path is absent | cleanup failed: locked | cleanup failed: locked |
| database.initialize | Database | WARNING | 0.212s | completed | cleanup failed: locked | cleanup failed: locked |
| import.preview | Import | WARNING | 0.000s | completed | cleanup failed: locked | cleanup failed: locked |
| import.confirmed | Import | WARNING | 0.084s | completed | cleanup failed: locked | cleanup failed: locked |
| crud.person-create-edit | CRUD | WARNING | 0.009s | completed | cleanup failed: locked | cleanup failed: locked |
| relationships.family-parent-child | Relationships | WARNING | 0.009s | completed | cleanup failed: locked | cleanup failed: locked |
| relationships.partner | Relationships | WARNING | 0.011s | completed | cleanup failed: locked | cleanup failed: locked |
| crud.event | CRUD | WARNING | 0.008s | completed | cleanup failed: locked | cleanup failed: locked |
| sources.citation | Sources | WARNING | 0.028s | completed | cleanup failed: locked | cleanup failed: locked |
| attachments.metadata | Attachments | WARNING | 0.010s | completed | cleanup failed: locked | cleanup failed: locked |
| undo-redo.person | Undo/Redo | WARNING | 0.034s | completed | cleanup failed: locked | cleanup failed: locked |
| analysis.validation | Analysis | WARNING | 0.002s | validation report produced | cleanup failed: locked | cleanup failed: locked |
| analysis.intelligence | Analysis | WARNING | 0.000s | intelligence report and disposition sidecar produced | cleanup failed: locked | cleanup failed: locked |
| analysis.cancellation | Analysis | WARNING | 0.000s | cancellation raised without starting a background task | cleanup failed: locked | cleanup failed: locked |
| analysis.source | Analysis | WARNING | 0.000s | source analysis report and disposition sidecar produced | cleanup failed: locked | cleanup failed: locked |
| visualization.timeline | Visualization | WARNING | 0.000s | timeline model and view produced | cleanup failed: locked | cleanup failed: locked |
| visualization.tree | Visualization | WARNING | 0.000s | tree model and layout sidecar produced | cleanup failed: locked | cleanup failed: locked |
| visualization.map | Visualization | WARNING | 0.000s | map model and view produced | cleanup failed: locked | cleanup failed: locked |
| persistence.research | Persistence | WARNING | 0.000s | research workspace reloaded | cleanup failed: locked | cleanup failed: locked |
| persistence.sidecars | Persistence | WARNING | 0.000s | temporary sidecar JSON files produced | cleanup failed: locked | cleanup failed: locked |
| export.formats | Export | WARNING | 0.008s | completed | cleanup failed: locked | cleanup failed: locked |
| backup.restore | Backup/Restore | WARNING | 0.021s | completed | cleanup failed: locked | cleanup failed: locked |
| startup.restart | Startup | WARNING | 0.001s | completed | cleanup failed: locked | cleanup failed: locked |
| packaging.resources | Packaging | WARNING | 0.000s | completed | cleanup failed: locked | cleanup failed: locked |
| packaging.version | Packaging | WARNING | 0.000s | completed | cleanup failed: locked | cleanup failed: locked |
| database.configured-checksum | Database | PASS | 0.000s | before=missing; after=missing |  | cleanup failed: locked |
