from repository.person_repository import PersonRepository
from config import DB_NAME

repo = PersonRepository(DB_NAME)
rows = repo.list_people()
print('count', len(rows))
print('first_type', type(rows[0]).__name__ if rows else None)
print('first_repr', repr(rows[0]) if rows else None)
print('first_len', len(rows[0]) if rows else None)
repo.close()
