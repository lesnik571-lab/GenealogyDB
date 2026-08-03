# GenealogyDB Beta Readiness

## Release Recommendation

NOT READY

## Passed checks
- **Duplicate Viewer buttons** (Accessibility): No duplicate button labels.
- **Duplicate menu commands** (Accessibility): No duplicate menu command labels.
- **Escape dialog close support** (Accessibility): Escape close bindings are present.
- **Keyboard shortcuts registered** (Accessibility): Keyboard shortcut bindings are registered.
- **Commands referencing missing functions** (Build status): All self command targets are defined.
- **Plugin loading** (Build status): Plugin sources passed static loading validation.
- **Python compilation** (Build status): Core Python modules compile.
- **Release resources** (Build status): All required resources are present.
- **Stale Viewer imports** (Build status): No proven unused Viewer imports found.
- **Tkinter windows during import** (Build status): No Tkinter windows are created at module import time.
- **Unavailable services** (Build status): All referenced Viewer services are initialized or lazily provided.
- **Viewer service failure handling** (Build status): Service failures use the logged, guarded concise Russian error path.
- **Configuration validity** (Data safety): Configuration JSON is valid.
- **Direct SQL in viewer.py** (Data safety): No direct repository SQL calls found.
- **UI and diagnostic sidecars** (Data safety): Sidecars are valid or absent and services recreate their directories on write.
- **Writable user-data directories** (Data safety): Data, log, plugin, and backup folders are writable.
- **Bounded caches** (Performance): Available.
- **Startup instrumentation** (Performance): Available.
- **Task Manager cancellation** (Performance): Available.
- **Full test-suite status** (Test status): 50 focused test modules are available; canonical execution is performed by the release workflow.

## Warnings
- **Toolbar scaling smoke check** (Accessibility): Manual verification remains required at 125%, 150%, and 175% scaling.
- **Unresolved crash diagnostics** (Build status): Tracebacks found in: genealogydb.log, genealogydb.log.1
- **Unresolved critical validation issues** (Data safety): Validation check unavailable: 'types.SimpleNamespace' object has no attribute 'conn'
- **Performance baseline availability** (Performance): Performance baseline has not been saved.
- **Person-list pagination** (Performance): Static smoke check did not find expected instrumentation.
- **Version consistency** (Version consistency): USER_MANUAL.md: version 1.0.0 not mentioned; CHANGELOG.md: version 1.0.0 not mentioned

## Blockers
- **Database backup capability** (Data safety): Файл базы не содержит ожидаемых таблиц: families, family_children, geocoding_cache, people, person_events, person_media, person_sources
- **Database integrity** (Data safety): Файл базы не содержит ожидаемых таблиц: families, family_children, geocoding_cache, people, person_events, person_media, person_sources

## Deferred items
- Manual UI scaling and interaction verification remains required.
