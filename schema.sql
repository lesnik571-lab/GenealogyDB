DROP TABLE IF EXISTS citations;
DROP TABLE IF EXISTS sources;
DROP TABLE IF EXISTS person_sources;
DROP TABLE IF EXISTS person_media;
DROP TABLE IF EXISTS person_events;
DROP TABLE IF EXISTS geocoding_cache;
DROP TABLE IF EXISTS family_children;
DROP TABLE IF EXISTS families;
DROP TABLE IF EXISTS people;

CREATE TABLE people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gedcom_id TEXT UNIQUE,
    first_name TEXT,
    last_name TEXT,
    sex TEXT,
    birth_date TEXT,
    birth_place TEXT,
    death_date TEXT,
    death_place TEXT,
    occupation TEXT,
    note TEXT
);

CREATE TABLE families (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gedcom_id TEXT UNIQUE,
    husband_id TEXT,
    wife_id TEXT,
    relationship_type TEXT NOT NULL DEFAULT 'unknown'
);

CREATE TABLE family_children (
    family_id TEXT,
    child_id TEXT
);

CREATE TABLE person_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_date TEXT,
    event_place TEXT,
    description TEXT,
    FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
);

CREATE TABLE person_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    media_type TEXT NOT NULL CHECK(media_type IN ('photo', 'document')),
    title TEXT,
    file_path TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
);

CREATE TABLE person_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL,
    title TEXT,
    source_url TEXT,
    archive_reference TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT,
    publication TEXT,
    repository_name TEXT,
    call_number TEXT,
    source_url TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('person', 'family', 'event', 'relationship')),
    target_id TEXT NOT NULL,
    page TEXT,
    quality TEXT,
    transcription TEXT,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE geocoding_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_place TEXT NOT NULL UNIQUE,
    original_place TEXT,
    latitude REAL,
    longitude REAL,
    status TEXT NOT NULL DEFAULT 'missing',
    provider TEXT,
    error_message TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_people_last_first_name
    ON people(last_name, first_name);

CREATE INDEX idx_people_gedcom_id
    ON people(gedcom_id);

CREATE INDEX idx_families_husband_id
    ON families(husband_id);

CREATE INDEX idx_families_wife_id
    ON families(wife_id);

CREATE INDEX idx_families_spouses
    ON families(husband_id, wife_id);

CREATE INDEX idx_family_children_family_id
    ON family_children(family_id);

CREATE INDEX idx_family_children_child_id
    ON family_children(child_id);

CREATE INDEX idx_family_children_relation
    ON family_children(family_id, child_id);

CREATE INDEX idx_person_media_person_id
    ON person_media(person_id);

CREATE INDEX idx_person_sources_person_id
    ON person_sources(person_id);

CREATE INDEX idx_citations_source_id
    ON citations(source_id);

CREATE INDEX idx_citations_target
    ON citations(target_type, target_id);

CREATE INDEX idx_sources_repository
    ON sources(repository_name);

CREATE INDEX idx_geocoding_cache_normalized_place
    ON geocoding_cache(normalized_place);
