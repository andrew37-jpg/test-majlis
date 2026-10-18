# Команды и воспроизведение аудита

Все проверки относятся к 18.10.2026. Нельзя запускать проектный pytest с рабочей БД:
fixture очищает таблицы в указанной *_test базе. В этом аудите использован отдельный
временный контейнер `majlis-audit-20261018`, localhost:55438, только фиктивные данные.
Пароль `audit_only` в примерах — заведомо вымышленный пароль временного контейнера,
не ключ MAJLIS. Рабочая конфигурация не переносилась.

## Пути инструментов

```sh
AUDIT_ROOT=/Users/andreyromanov/Documents/Codex/2026-10-14/new-chat
AUDIT_PROJECT="$AUDIT_ROOT/work/majlis-audit-2026-10-18/project"
AUDIT_PY="$AUDIT_ROOT/work/majlis-audit/tools/bin/python"
AUDIT_NODE=/Users/andreyromanov/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
```

Инвентаризация: `git rev-parse --show-toplevel` в рабочем проекте вернул «not a git repository»;
исходники скопированы без .env/data/.run/cache, SHA-256 сохранены отдельно. Поиск файлов — rg;
инструкции AGENTS.md проверены по цепочке родительских каталогов.

## Изолированная БД (фактически выполнено)

```sh
docker run -d --name majlis-audit-20261018 \
  -e POSTGRES_PASSWORD=audit_only -e POSTGRES_DB=majlis_audit_test \
  -p 127.0.0.1:55438:5432 --tmpfs /var/lib/postgresql/data postgres:17-alpine

docker exec -i majlis-audit-20261018 psql -U postgres -d majlis_audit_test -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE audit_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'audit_only';
CREATE ROLE audit_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD 'audit_only';
ALTER DATABASE majlis_audit_test OWNER TO audit_migrator;
ALTER SCHEMA public OWNER TO audit_migrator;
REVOKE ALL ON DATABASE majlis_audit_test FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE majlis_audit_test TO audit_app;
GRANT USAGE ON SCHEMA public TO audit_app;
SQL
```

Для повторения используйте свободное имя контейнера и порт. Существующий контейнер после
аудита остановлен; tmpfs не является долговременным хранилищем. Сохранённые dumps содержат
только вымышленные данные. Не применять команды к `majlis-local-db-1`.

## Тесты и анализаторы

Рабочая папка для pytest/coverage — AUDIT_PROJECT. Тестовый .env в копии содержит
синтетический bot token, отдельные URLs на localhost:55438 и HEALTH_TOKEN/INTEGRATION_SECRET
из повторяемых тестовых символов. Он не пригоден для production и не заимствован из рабочего .env.

```sh
cd "$AUDIT_PROJECT"
TEST_DATABASE_URL=postgresql://audit_app:audit_only@127.0.0.1:55438/majlis_audit_test \
TEST_MIGRATION_DATABASE_URL=postgresql://audit_migrator:audit_only@127.0.0.1:55438/majlis_audit_test \
  "$AUDIT_PY" -m pytest -q -p no:cacheprovider

"$AUDIT_NODE" --test tests/js/*.test.mjs

# Из AUDIT_ROOT (анализ только копии):
work/majlis-audit/tools/bin/ruff check --no-cache work/majlis-audit-2026-10-18/project
work/majlis-audit/tools/bin/bandit -r work/majlis-audit-2026-10-18/project/majlis \
  work/majlis-audit-2026-10-18/project/main.py work/majlis-audit-2026-10-18/project/manage.py \
  -f json -o outputs/MAJLIS-Audit-2026-10-18/evidence/bandit.json
```

Coverage: тот же pytest с теми же TEST_* переменными, команда:

```sh
"$AUDIT_PY" -m coverage run --branch --source=majlis,main,manage -m pytest -q -p no:cacheprovider
"$AUDIT_PY" -m coverage json -o "$AUDIT_ROOT/outputs/MAJLIS-Audit-2026-10-18/evidence/coverage.json"
```

## Красные регрессионные проверки

Файлы находятся отдельно: `work/majlis-audit-2026-10-18/checks/`.

```sh
cd "$AUDIT_ROOT/work/majlis-audit-2026-10-18/checks"
PYTHONPATH=../project:../project/tests \
TEST_DATABASE_URL=postgresql://audit_app:audit_only@127.0.0.1:55438/majlis_audit_test \
TEST_MIGRATION_DATABASE_URL=postgresql://audit_migrator:audit_only@127.0.0.1:55438/majlis_audit_test \
  "$AUDIT_PY" -m pytest -q -p no:cacheprovider test_audit_regressions.py
"$AUDIT_NODE" --test feed-race.test.mjs
```

Ожидаемый исход на аудированной версии: 4 Python failures и 1 JS failure.
Это проверки правильного поведения, а не проходящие тесты, подтверждающие ошибку.
Не выполнять одновременно с browser fixture или другой серией, использующей ту же тестовую БД.

## Чистая установка и vulnerability database

```sh
python -m venv "$AUDIT_ROOT/work/majlis-audit-2026-10-18/install-venv"
"$AUDIT_ROOT/work/majlis-audit-2026-10-18/install-venv/bin/python" -m pip install \
  --no-cache-dir --require-hashes -r requirements.lock
"$AUDIT_ROOT/work/majlis-audit-2026-10-18/install-venv/bin/python" -m pip check

"$AUDIT_ROOT/work/majlis-audit/tools/bin/pip-audit" -r requirements.lock \
  --no-deps --disable-pip --format=json --output "$AUDIT_ROOT/outputs/MAJLIS-Audit-2026-10-18/evidence/dependency-audit.json"
```

Передавались только публичные имена/версии зависимостей. Реальные ключи не проверялись;
не выполнялись Bot API sendMessage/configure/getMe с рабочим токеном.

## Upgrade / restore

Для upgrade в том же временном контейнере создана отдельная `majlis_upgrade_test`, owner
`audit_migrator`. Через psycopg выполнен SQL 001, создан schema_migrations с SHA256 файла,
вставлены buyer 3001, заказ UPGRADE-TEST на 12345 копеек и событие Original history.
Затем Settings направлены только на эту БД и вызван `Database.migrate()` дважды.
Результат и сохранённые значения — evidence/upgrade.json. Это репетиция, не реальная старая БД.

В контейнере дополнительно создана роль `majlis SUPERUSER LOGIN` для совместимости с
существующим manage.py, где имя административного пользователя зафиксировано.
Это роль только временного контейнера, не расширение доступа рабочей среды.

```sh
cd "$AUDIT_PROJECT"
"$AUDIT_PY" manage.py backup --container majlis-audit-20261018 \
  --file "$AUDIT_ROOT/outputs/MAJLIS-Audit-2026-10-18/evidence/representative.dump"
"$AUDIT_PY" manage.py restore-test --container majlis-audit-20261018 \
  --file "$AUDIT_ROOT/outputs/MAJLIS-Audit-2026-10-18/evidence/representative.dump" \
  --target majlis_restore_representative_test
```

Перед вторым backup все 13 таблиц заполнены синтетическими строками: в частности,
manager_access, order_managers, customer_messages, legacy_imports, integration_events,
rate_limits и bot_state. В source не было конкурентных записей. Для повторного запуска
backup требуется новое имя файла, restore — новое имя *_test БД: существующие не перезаписываются.

Дополнительный probe A11: после создания двух изолированных DB Settings.migration_database_url
указывает на majlis_upgrade_test, Database.url на majlis_audit_test; migrate() ошибочно возвращается
успешно. См. evidence/migration-target-mismatch.json. Рабочие адреса не участвовали.

## Browser fixture и CSP

```sh
cd "$AUDIT_ROOT/work/majlis-audit-2026-10-18"
"$AUDIT_PY" browser_fixture.py
```

API8910, proxy8911; синтетическая личность9001, manager grant только в тестовой БД.
В HTTP-ответах сохранены реальные security headers backend. Внешний Telegram SDK заменён
на same-origin фиктивный SDK только в ответе proxy. Код приложения не менялся.
Ответ `/api/manager/activity` задерживается на 2 секунды.

Через CUA открыта /manager/, viewport390x844: Activity→Inbox оставляет «Загружаем…» после
окончания первого запроса; ручное «Обновить» восстанавливает. SDK diagnostic listener
securitypolicyviolation фиксирует style-src-elem/inline/nav-lens.js:133. Скриншоты/JSON сохранены.
Browser evaluate применялся только для чтения DOM; никаких реальных сообщений/платежей.

## Compose и cleanup

В AUDIT_PROJECT с фиктивными APP_DOMAIN, POSTGRES_*PASSWORD:

```sh
docker compose --env-file .env -f compose.server.yaml config --quiet
```

Никакого `up` серверного профиля, DNS, TLS или смены кнопки рабочего бота.
Тестовые API/proxy завершены, тестовые вкладки закрыты, viewport override сброшен.
Контейнер аудита остановлен. В конце повторно проверены хеши рабочего проекта и .env.
