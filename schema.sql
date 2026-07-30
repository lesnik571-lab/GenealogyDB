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
