import sqlite3
from config import DB_NAME
from repository.person_repository import PersonRepository

conn = sqlite3.connect(DB_NAME)
print('people_count', conn.execute('SELECT COUNT(*) FROM people').fetchone()[0])
repo = PersonRepository(DB_NAME)
rows = repo.list_people()
print('repo_rows', len(rows))
print('first', rows[:3])
repo.close()
conn.close()
