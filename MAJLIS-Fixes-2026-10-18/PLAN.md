# Исходная матрица исправлений

Все A01–A11 актуальны: исходные хеши совпадают с аудитом. Рабочие данные/конфигурация исключены из копии.

| ID | Причина | План | Критерий |
|---|---|---|---|
| A11 | Не сверяется endpoint/DB | Проверка до DDL, явные алиасы + server identity | Mismatch не меняет schema/ACL |
| A02 | count>0 | Точный реестр/checksum, 503 | Partial/corrupt/unknown/offline →503 |
| A01 | Порядок хранится на заказе | Контекст партии, граница возврата, receipt payload + явный replay | Seq100→seq1, возврат/гонки/дедупликация |
| A04 | Общий busy | Владелец запросов по лентам | Новая лента загружается независимо |
| A05 | Динамический style запрещён | Внешние alias CSS | Нет CSP violation, computed styles совпадают |
| A06 | amount исключён из audit | Контролируемый before/after/diff | Деньги/actor/version сохранены атомарно |
| A03 | Потерянный ответ считается обычной ошибкой | Unknown + attempts + message_id + операторский retry | Неизвестное не переотправляется автоматически |
| A07 | Proxy peer общий | Проверенный ID отдельно, shared PostgreSQL limiter, trusted edge | Spoofing/реплики/восстановление |
| A08 | Нет SQL timeout | Runtime-only настройки + измерения | Timeout/rollback/reuse |
| A09 | Только имя исключения | JSON-safe context + request/job IDs | Ошибка диагностируется, секреты не выводятся |
| A10 | Mutable image tags | Получить проверенные registry digests + release metadata | Фактический registry proof, без выдуманных digest |

Новые миграции — только тестовая репетиция до отдельного подтверждения для рабочей БД.
