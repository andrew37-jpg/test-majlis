# MAJLIS 0.8 — отчёт об исправлении аудита A01–A11

Дата: 18 октября 2026. Текущий проект: `/Users/andreyromanov/Desktop/majlis_bot_test/majlis-platform`.
Это продолжение существующего проекта, без замены приложения новой реализацией.

## Итог и границы

Исправления перенесены в актуальный каталог PyCharm и повторно проверены именно там:
**140 Python-тестов, 42 JS-теста, Ruff без замечаний, форматирование 23 затронутых Python-файлов,
Bandit без находок**. Зафиксированные Python runtime-зависимости: pip-audit не обнаружил известных
уязвимостей на дату проверки. Это не гарантия отсутствия ошибок или уязвимостей.

`.env`, файлы `data/`, исходные миграции 001–007 и прежняя Run Configuration сохранены.
Пользовательские роли не менялись; Telegram API с настоящим ключом не вызывался.
Рабочая БД проверена только чтением: **7 установленных миграций, ожидается 9**.
Новые миграции в неё НЕ применялись. Новая версия в рабочем Telegram НЕ запущена.
Обычный launcher больше не обновляет схему автоматически; перед запуском требуется отдельно
согласовать backup и применение 008/009. Это прямое ограничение пользовательского задания.

Docker runtime-образ собран и запущен отдельно с фиктивным ключом и тестовой БД:
`/health`200, `/ready`200, HTML200 с настоящей CSP, UID10001, pip/ensurepip отсутствуют.
`/ops/health`503 корректно обнаружил отсутствие bot/notify: они намеренно не запускались.
Скан образа оставляет **28 находок: 1 High, 1 Medium, 26 Low**, см. раздел supply chain.

## Основание и сохранность

Исходный отчёт: [AUDIT_REPORT](../MAJLIS-Audit-2026-10-18/AUDIT_REPORT.md), не изменялся.
Исходные 111 Python/38 JS тестов повторены до правок: [Python](evidence/baseline-python.txt),
[JS](evidence/baseline-js.txt). На старте проект не отличался от зафиксированной версии аудита;
перед переносом также не было параллельных изменений.

Исходные хеши — [before.json](evidence/before.json); адресный состав и хеши изменений —
[deploy-manifest.json](evidence/deploy-manifest.json); исходные версии изменённых файлов —
[backup](backup/). Секреты и рабочие данные в резервную копию исходников не включались.
Исходные аудиторские сценарии находятся в `work/majlis-audit-2026-10-18/checks/`;
новые регрессии — в `tests/test_audit_fixes.py`, JS — `tests/js/feed-requests.test.mjs`.
Исправление A03 намеренно уточняет контракт аудиторского теста, а не обещает exactly once.

## Матрица A01–A11

| ID | Причина → исправление | Основные файлы | Проверка и статус |
|---|---|---|---|
| A01 | Последовательность/свежесть старой партии сохранялись. Добавлены generation, история привязок, high-water на поток default-provider/shipment; свежесть сбрасывается при новой привязке, статус сохраняется. Возврат к старой партии требует source occurred_at не ранее новой привязки. Явный replay с ACL, версией и ledger. | `integrations.py`, `services.py`, `legacy_import.py`, `schemas.py`, миграция008, `order-editor.js` | Старый100 → новый1; повтор/старое событие; возврат; delivered; replay/ACL/повтор; конкурентный webhook/rebind. **Исправлено и проверено** на синтетике. Реальный provider ещё не подключён. |
| A02 | Проверялась лишь непустота реестра. Readiness сравнивает полный набор имён и SHA256; missing/partial/modified/unknown/DB outage →503, без DDL. | `database.py`, `api.py`, `main.py` | Параметризованные реестры, offline503, полная схема200, запрет неявной миграции launcher. **Исправлено и проверено**. |
| A03 | После потери ответа/commit lease мог привести к повторной отправке. Добавлены unknown, message_id, попытки, операторский retry и общий cooldown429. | `notifications.py`, `bot.py`, `api.py`, `schemas.py`, `workspace.js`, `app.js`, миграция008 | Сбой до отправки; неизвестный исход; ответ до failed commit; истечение lease; конкуренция workers; owner transfer; ручной retry/ACL;429. **Снижен риск, ограничение сохраняется**: неизвестный внешний исход нельзя локально сделать exactly once. |
| A04 | Общий feedBusy блокировал новый раздел, старый finally мог затронуть новую загрузку. FeedRequests ведёт отдельные запросы/контроллеры, отклоняет устаревшие ответы, отменяет при очистке прав. | `workspace.js`, `app.js`, `tests/js/feed-requests.test.mjs` |42 JS теста, включая задержки/ошибки/refresh/pagination/late finally; браузер activity→inbox→activity без ручного восстановления. **Исправлено и проверено**. |
| A05 | Динамический STYLE алиасов блокировался CSP. Перенесены стабильные ID/data-lens-source-id селекторы во внешний CSS с сохранением specificity. | `nav-lens.js`, `style.css` | Одинаковые проверенные computed properties источника/копии в двух темах, нет duplicate ID/нового STYLE; автономный сценарий без CSP events; geometry/gestures. **Реализовано, внешняя проверка не выполнена**: Telegram iOS/Android не проверены. |
| A06 | В before/after отсутствовала сумма. Аудит включает amount, изменённые имена полей; секретные invoice URL и произвольные тексты не записываются. | `services.py` |12001→13000 копеек,409, отсутствие URL, rollback суммы/события при отказе audit insert; runtime UPDATE/DELETE audit запрещены. **Исправлено и проверено**. |
| A07 | Peer общего proxy объединял пользователей. Проверенная Telegram identity имеет свою корзину; анонимный peer или header только от exact trusted proxy. Shared PostgreSQL buckets обеспечивают общий лимит реплик. | `limiting.py`, `http_boundary.py`, `config.py`,008, Caddy/Compose | Два подключения/экземпляра лимитера, разные подписанные users, spoofed headers, окно/ёмкость, fail-closed503. **Реализовано, внешняя проверка не выполнена**: серверная сеть и реальные replicas не предоставлены. |
| A08 | SQL мог долго занимать соединения. Runtime options statement5000/lock1000/idle transaction15000ms; не переносятся на migrate/backup. Безопасный API503. | `database.py`, `config.py`, `api.py` | Реальные долгий SELECT/row lock/idle termination; rollback и pool recovery; измерения25 000orders. **Исправлено и проверено**. |
| A09 | Не было связной безопасной диагностики. JSON allowlist, request/job IDs, тип/locations исключения без текста/locals, безопасная клиентская ошибка. | `observability.py`, `http_boundary.py`, `api.py`, `cli.py`, `notifications.py` | Контролируемый DB failure, X-Request-ID/error ID, синтетический секрет не попадает в логи/ответы; сетевые сбои Telegram имитированы. **Исправлено и проверено** в изолированном стенде. |
| A10 | Mutable base tags. Проверены и закреплены index digests Python/Postgres/Caddy, build ID, SBOM, CVE, release/rollback procedure; pip удалён из runtime. | Dockerfile, Postgres.Dockerfile, Compose, `api.py`, `docs/RELEASE-0.8.md` | Registry ARM64/AMD64, локальные ARM64 build/smoke,154 SBOM packages,28 CVE findings. **Снижен риск, ограничение сохраняется**: OS findings, внешняя архитектура и непобитовая воспроизводимость. |
| A11 | Runtime и migrator могли менять разные базы/ACL. До DDL/GRANT сравниваются endpoint/db/разные явные роли, exact aliases и database identity через оба соединения. | `migration_safety.py`, `database.py`, `config.py` | Разные базы/endpoints, aliases, одинаковые/разные роли, offline, ошибочная server identity; отказ до yield/DDL. **Исправлено и проверено**. |

Названия файлов Python в таблице относятся к каталогу `majlis/`, если не указан иной путь.
Все новые контракты проверялись на PostgreSQL17, а не на SQLite или моках базы.

## Миграции и уведомления

008 добавляет контексты партий, payload receipts/replay ledger, attempts/message_id/unknown,
shared buckets и heartbeat. Старые `sending` становятся `unknown` с upgrade_unresolved;
автоматически заново не отправляются. Суммы, статусы, история сохраняются.
009 добавляет privacy marker и приватный журнал локальных операторов; сама не удаляет ПД.

Успех Telegram признаётся только при корректном message_id. Предсетевая connect/pool ошибка
допускает ограниченный retry; явный429 задаёт общий cooldown;400/403 терминальны;
401/409 останавливают worker после записи результата. Read/write/protocol timeout, потерянный
ответ и незавершённый sending после lease →unknown без автоматического повтора.
Потеря commit после подтверждённого сообщения также приводит к unknown, а не sent.

Оператор смотрит историю попыток в менеджерском разделе уведомлений или защищённом API,
сверяет переписку, затем решает о повторе. Retry требует edit ACL конкретного заказа,
совпадения попыток/владельца/generation и явного принятия риска дубля. Решение журналируется.
В unknown возможна недоставка; после ручного retry возможен дубль. Политику рабочего запуска
должен утвердить владелец. Старый аудиторский A03 заменён проверками этого явного контракта;
требование универсального exactly once не объявляется выполненным.

Для старых integration receipts без payload допускается подписанная идентичная повторная
доставка с совпадающим непустым hash. Пустой старый hash не позволяет достоверно восстановить
payload: нужен проверенный источник/новое событие. Массового удаления receipts нет.

## Фактически выполненные проверки

- [Итоговый Python на исходниках PyCharm](evidence/final-actual-python.txt):140passed.
  Прибавка к исходным111 =29 параметризованных случаев; не29 отдельных функций.
- [Итоговый JS на исходниках PyCharm](evidence/final-actual-js.txt):42passed,0failed;
 42−38 =4 новых теста управления лентами.
- [Ruff](evidence/final-actual-ruff.txt), [format](evidence/final-actual-format.txt),
  [Bandit JSON](evidence/final-actual-bandit.json), [pip-audit](evidence/final-pip-audit.json).
- [Проверка настроек реальным интерпретатором PyCharm](evidence/final-actual-check.txt):
  `main.py --check` успешен, соединений/Telegram вызовов этот режим не делает.
- [Upgrade rehearsal](evidence/upgrade-rehearsal.json): схема7→9, повторный migrate,
  сохранённые сумма12001/статус/history, sending→unknown. [Restore](evidence/restore.txt):
 20таблиц,63constraints,2functions, совпадение данных/структуры,30 негативных проверок ограничений.
  Pre-upgrade dump восстановлен в отдельную новую rollback *_test БД со схемой7.
  Ownership/ACL pg_restore --no-owner/--no-acl не проверяет; роли проверяются отдельными тестами.
- [Benchmark](evidence/benchmark.json):25 000 синтетических заказов,100клиентов,20замеров:
  median/p95 count12.80/18.35ms; substring105.50/111.23ms; buyer7.65/12.03ms.
  Это локальные измерения, не нагрузочный SLA. Планы сохранены, произвольные индексы не добавлены.
- [Docker build](evidence/final-docker-build.txt), [container smoke](evidence/final-container-smoke.json),
  Compose config --quiet успешен; [Caddy adapt --validate](evidence/final-caddy.txt) успешен,
  есть предупреждение о форматировании Caddyfile, синтаксической ошибки нет.

### Браузер/CSP

Изолированный API с настоящими security headers и синтетическим Telegram SDK/user9001,
задержкой activity2s; настоящий bot token не использовался.
[Feed race](evidence/feed-fixed.json), [light styles](evidence/browser-optics-light.json),
[dark styles](evidence/browser-optics-dark.json), [autonomous CSP](evidence/csp-autonomous.json),
[geometry](evidence/menu-dark-geometry.json), [light screenshot](evidence/browser-light.png),
[dark screenshot](evidence/browser-dark.png).

У source/mirror совпали проверенные color/background/font-size/padding/border-radius.
В автономном сценарии приложения CSP events отсутствовали. В диагностике, собранной рядом
с действиями UI-инструмента, есть отдельное source-empty inline-style violation; его источник
достоверно не установлен. Поэтому отчёт не утверждает «во всех взаимодействиях0violations».
Динамическая вставка STYLE из приложения удалена; CSP не ослаблена.
Браузерная проверка менеджерского кабинета не является повторной приёмкой точного сходства
анимации с iOS. Telegram на реальных iOS/Android после правок не проверялся.

## Supply chain и остающиеся находки

Проверенные index digests содержат ARM64/AMD64:

- Python3.12-slim: `sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
- Postgres17-alpine: `sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`.
- Caddy2-alpine: `sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f`.

[Registry evidence](evidence/image-pins.json). Финальный app image:
`majlis-platform:audit-fixes-20261018`, BUILD_ID `majlis-0.8-audit-fixes-20261018`.
[SBOM](evidence/final-app-sbom.spdx.json):154packages.
[SARIF](evidence/final-app-cves.sarif.json):28findings =1High+1Medium+26Low.

High — CVE-2026-85091 zlib: [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-85091)
на дату проверки помечает пакет trixie vulnerable/unfixed и отдельно обсуждает затронутый диапазон.
Исправленного пакета для включения в эту сборку не подтверждено. Эксплуатируемость именно
MAJLIS этим сканом не доказана; требуется vendor update/разбор достижимости и решение владельца.
Medium — CVE-2025-45582 tar: [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2025-45582)
отмечает спорность проблемы и соответствие документированному поведению upstream.
Это сохранённая находка сканера, не доказанная уязвимость HTTP-сценария MAJLIS.
Остальные26Low не скрыты, полный перечень в SARIF.

Удаление pip снизило набор находок предыдущего candidate-образа, но не исправило пакеты ОС.
Сканировался финальный app runtime; отдельные полные CVE-сканы Postgres/Caddy здесь не выполнялись.
Их digests и Caddy config проверены. Внешняя серверная архитектура/развёртывание не предоставлены.
Фиксация base image не делает сборку побитово воспроизводимой: metadata, tooling,
platform-specific wheels и dev dependencies имеют отдельные ограничения; см.RELEASE-0.8.md.

## Эксплуатационные средства и решения владельца

Добавлены private /ops/health и CLI monitor/worker-health, heartbeat и очередь failed/unknown,
Compose healthchecks. Unhealthy контейнер сам по себе не перезапускается: нужны оператор/alerts.
Шаблоны cron не установлены, backup helper выполняет backup0600 и retention-preview без удаления.

Privacy plan/экспорт по умолчанию dry-run; запись/редактирование требуют локального оператора
из PRIVACY_OPERATOR_IDS, проверенного migrator, актуального digest и явных флагов.
Файл экспорта эксклюзивный0600, без payment secrets; процедура ограничена клиентскими записями.
Псевдонимизация очищает личные поля/тексты/ссылки и прекращает их автоматический повторный сбор,
но сохраняет Telegram IDs, бизнес-данные, audit/provenance и backups. Это НЕ полная анонимизация.
Реальная выгрузка/удаление не выполнялись; тесты использовали только вымышленные данные.

Владелец должен определить: retention/правомерный scope/RPO/RTO/offsite encryption,
privacy operators/адресатов alerts, политику unknown, постоянный HTTPS/сервер/подсеть,
поддержку, реальные payment/cargo контракты, данные старой базы и процедуру миграции.
Ручная платёжная ссылка не выдаётся за подтверждение оплаты; синтетический импорт не является
переносом старой базы. Production/реальные платежи/уведомления не проверены.

## Как проверить и запустить в PyCharm

Каталог: `/Users/andreyromanov/Desktop/majlis_bot_test/majlis-platform`.
Интерпретатор: `/Users/andreyromanov/Desktop/majlis_bot_test/.venv/bin/python`.
Новые `.run/MAJLIS 0.8 Check.run.xml` и `.run/MAJLIS 0.8 MiniApp.run.xml` указывают на
этот main.py и рабочую папку явно; старую конфигурацию не удаляли. Файлы проверены,
нажатие Run в UI PyCharm и рабочий Telegram запуск не выполнялись.

1. Откройте проект; выберите `MAJLIS 0.8 Check`. Это безопасная проверка настроек.
2. До следующего шага отдельно подтвердите обновление рабочей базы, остановите writers,
   сделайте backup и проверьте его restore в новой *_test БД по UPGRADE-0.8.md.
3. Из каталога проекта с указанным интерпретатором выполните явно:

```sh
python -m majlis.cli migrate
```

4. Затем `MAJLIS 0.8 MiniApp` / `python main.py`. Без шага3 запуск сейчас откажет:
   рабочая схема7, ожидается9. Основные секреты/DSN уже сохранены, в чат их не отправлять.
5. Проверьте ready/ops health с локальным health token, актуальный HTTPS и кнопку бота.
   Временный localhost.run остаётся нестабильным тестовым адресом, не постоянным hosting.

Новые параметры/defaults: DB_STATEMENT_TIMEOUT_MS5000, DB_LOCK_TIMEOUT_MS1000,
DB_IDLE_TRANSACTION_TIMEOUT_MS15000, SHARED_PUBLIC_REQUESTS_PER_MINUTE240,
WORKER_STALE_SECONDS90; TRUSTED_PROXY_CIDRS/MIGRATION_ENDPOINT_ALIASES/PRIVACY_OPERATOR_IDS
по умолчанию пусты. .env.example обновлён, рабочий .env не менялся.
Серверный профиль задаёт exact Caddy proxy CIDR; до production проверить его реальную сеть.

### Повторить проверки

Только с отдельной *_test БД и двумя ролями: инструкции TESTING.md.
Фактические команды этой проверки:

```sh
TEST_DATABASE_URL=<isolated runtime URL> TEST_MIGRATION_DATABASE_URL=<same DB migrator URL> python -m pytest -q -p no:cacheprovider
node --test tests/js/*.mjs
ruff check --no-cache .
ruff format --check --no-cache <23 changed Python files>
bandit -r majlis main.py manage.py ops -q -f json
pip-audit -r requirements.lock --no-deps --disable-pip --format json
python main.py --check
docker build --build-arg BUILD_ID=majlis-0.8-audit-fixes-20261018 -t majlis-platform:audit-fixes-20261018 .
docker scout sbom local://majlis-platform:audit-fixes-20261018 --format spdx --output sbom.json
docker scout cves local://majlis-platform:audit-fixes-20261018 --format sarif --output cves.json
```

TEST_* указывали на изолированный контейнер `majlis-fixes-20261018`, порт55439;
рабочий PostgreSQL55437 тестами не очищался. Перед actual-source тестами BOT_TOKEN,
DATABASE_URL/MIGRATION_DATABASE_URL/HEALTH_TOKEN и другие подключения переопределялись
фиктивной тестовой конфигурацией процесса; .env не перезаписывался.

## Откат

До применения миграций возможен адресный возврат исходников из `backup/` по before/after
манифесту; новые файлы должны удаляться только по списку manifest с before:null и после
проверки отсутствия новых пользовательских изменений. Не копируйте .env/данные из чужих копий.
Не выполняйте массовое зеркалирование каталога или удаление таблиц.

После обновления рабочей БД откат требует остановки writers, аварийной копии и восстановления
pre-upgrade dump в НОВУЮ БД, проверки ролей и решения владельца о post-backup данных/RPO.
Старый notify не запускать на новой очереди. Проверенный синтетический rollback не означает
выполненный рабочий rollback. Полная последовательность: docs/UPGRADE-0.8.md.
