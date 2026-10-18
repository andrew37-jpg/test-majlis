# Повторение проверок

Python 3.12 + PostgreSQL 17. Python зависимости: requirements.lock и requirements-dev.txt. JavaScript тесты используют только встроенный node:test (Node 22+), runtime Mini App не требует Node.

Администратор создаёт НОВУЮ `majlis_example_test`, две отдельные LOGIN-роли без superuser/createdb/createrole: migrator владеет этой БД и схемой public, runtime получает CONNECT и USAGE. PUBLIC не получает CREATE. Не используйте рабочую БД и не назначайте ролям доступ к ней.

Локально задайте `TEST_DATABASE_URL` (runtime), `TEST_MIGRATION_DATABASE_URL` (migrator) и `.env` по примеру. Оба URL должны указывать на ОДНУ тестовую базу и различаться ролями. Fixtures проверяют суффикс `_test`, соответствие пары и не используют мигратор рабочей БД. Fixtures очищают только выбранную тестовую БД, поэтому она должна быть выделенной и одноразовой.

```sh
python -m pytest -q tests
node --test tests/js/*.mjs
ruff check .
ruff format --check .
bandit -r majlis main.py manage.py
```

Ruff/Bandit нужны только для разработки (requirements-dev.txt). Telegram HTTP, SSH и запускаемые процессы в unit-тестах изолированы; PostgreSQL, миграции, роли, ограничения и конкурентные транзакции проверяются реально. Сетевой TLS/SSH и Telegram-платежи этими тестами не подтверждаются.

Для backup/restore используйте описанную процедуру в OPERATIONS.md. Отдельный restore-test не проверяет сохранение ACL: он проверяет данные/структуру, а права проверяются тестами runtime/migrator отдельно.
