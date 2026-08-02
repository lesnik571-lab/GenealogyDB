# GenealogyDB

GenealogyDB is a lightweight GEDCOM browser and importer for building and exploring a local family-tree database. The project now uses a modular architecture with a dedicated parser package, repository layer, and Tkinter-based viewer.

## Architecture

- Parser package: the GEDCOM parsing logic lives in the gedcom package, with parsing helpers in gedcom/records.py and the public entry point exposed from gedcom/__init__.py.
- Repository layer: SQL access is encapsulated in repository/person_repository.py and repository/database_repository.py so the UI and importer no longer directly manage SQLite queries.
- Viewer: the main desktop application is implemented in viewer.py and provides both a list view and a graphical family-tree view.
- Import workflow: importer.py loads GEDCOM data, clears existing records, and imports people and family relationships into the SQLite database.
- Database bootstrap: database.py initializes the schema from schema.sql and creates the local database under data/.

## Features

- Import GEDCOM files into SQLite
- Browse people from the database
- Open a person profile with birth, death, occupation, and note details
- Explore relationships for parents, spouses, children, and siblings
- Navigate related people directly from the details view
- Switch between a list view and an interactive graphical family-tree view
- Zoom and pan the tree canvas for easier navigation
- Automated regression tests with pytest

## Project layout

- app.py: terminal-based entry point for importing and opening the viewer
- config.py: database path and application version
- database.py: schema initialization entry point
- importer.py: GEDCOM import orchestration
- parser.py: compatibility wrapper for the parser package
- viewer.py: Tkinter GUI and family-tree canvas
- gedcom/: GEDCOM parsing package
- repository/: persistence layer for people and schema operations
- tests/: regression tests for parsing, import, repositories, navigation, and tree rendering

## Running the app

1. Create the database:
   - python database.py
2. Import a GEDCOM file:
   - python app.py
   - choose option 1 and enter the GEDCOM filename
3. Open the viewer:
   - python app.py
   - choose option 2

## Testing

Run the full test suite with:

- python -m pytest -q
