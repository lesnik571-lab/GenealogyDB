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
    wife_id TEXT
);

CREATE TABLE family_children (
    family_id TEXT,
    child_id TEXT
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
