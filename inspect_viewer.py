import sqlite3
from config import DB_NAME
from repository.person_repository import PersonRepository

repo = PersonRepository(DB_NAME)
rows = repo.list_people()
print('rows', len(rows))
for row in rows[:5]:
    print(row)
repo.close()
