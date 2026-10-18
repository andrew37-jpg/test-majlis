# Установка MAJLIS в PyCharm — 15.10.2026

Обновлён существующий проект `/Users/andreyromanov/Desktop/majlis_bot_test/majlis-platform`, основа — ранее подготовленная 0.6. В PyCharm выбрана конфигурация **MAJLIS 0.6 — Mini App**, интерпретатор `/Users/andreyromanov/Desktop/majlis_bot_test/.venv/bin/python`, cwd — majlis-platform. Запуск выполнен кнопкой Run в PyCharm. Для повторного запуска: Stop, затем зелёная кнопка этой конфигурации. Другой бот с тем же токеном одновременно не запускается.

## Сохранность и настройка

- Приватная копия прежнего проекта/.env/настроек IDE и pg_dump находятся в `/Users/andreyromanov/Documents/Codex/2026-10-14/new-chat/work/majlis-install/before-v06-20261015-004734`, каталог 0700, файлы 0600. Не отправляйте эти приватные архивы третьим лицам.
- Dump восстановлен только в новую `majlis_pre_v06_1792014454_test`: 9 таблиц, хеши строк/структура ограничений совпали, 10 отрицательных CHECK/FK/UNIQUE проверок отклонены. Первое сравнение строк CHECK отличалось из-за эквивалентных casts после pg_restore; это проверено сравнением структуры и реального поведения, не проигнорировано.
- Существующие BOT_TOKEN, runtime/admin пароли, Docker volume и MANAGER_IDS сохранены. Менеджер не назначен; покупательский доступ остаётся.
- Создана отдельная ограниченная роль majlis_migrator, ей передано владение только объектами базы majlis. Применены 001–004. Runtime majlis_app не владеет orders, не может CREATE в public, SELECT/UPDATE audit_log и SELECT legacy_imports запрещены. Новые migration/health секреты сохранены только локально в .env (0600).
- Установка зависимостей через requirements.lock --require-hashes; pip check: No broken requirements found. requirements.txt теперь ссылается на lock для удобного обычного pip install -r requirements.txt.

## Обнаруженная проблема запуска и исправление

localhost.run фактически возвращает HTTPS-адрес внутри JSON-события вместе с длинным ANSI QR-кодом. Парсер 0.6 отвергал data длиннее 4096 символов. Первый запуск из PyCharm поэтому завершился ошибкой «не выдал HTTPS-адрес». Регрессия воспроизведена: 1 failed / 3 passed. Установленный majlis/tunnel.py ограничивает весь JSON отдельно (128 KiB), data (64 KiB) и ищет URL лишь в первых 4096 символах. Allowlist доменов и границы hostname сохранены. После исправления 4/4 проходят; полный набор на отдельной тестовой PostgreSQL **91 passed in 24.38s**. Это не тесты на рабочей базе. [Патч](evidence/installation-fix.patch), [до](evidence/tunnel-before.txt), [после](evidence/tunnel-after.txt), [pytest](evidence/tests-latest.txt).

## Запуск и границы проверки

Локальные /health и /ready с health-token — 200. Публичные HTTPS /health и HTML — 200, instance_id совпадает с локальным API. getMe подтверждает @majlis_test_bot. Бот и отдельный notify worker запущены дочерними процессами PyCharm. После восстановления туннеля меню отдельно перепривязано к актуальному URL, setChatMenuButton=true и getChatMenuButton подтвердил совпадение. [HTTP и Telegram API](evidence/launch-verification.json).

В Telegram было открыто старое окно Mini App с прежним адресом; оно показывало ранее загруженные данные и ошибку обновления. Его нельзя считать проверкой нового адреса. Закройте это окно, откройте [@majlis_test_bot](https://t.me/majlis_test_bot) и актуальную кнопку меню «Кабинет MAJLIS» либо отправьте новый /start. Старые inline-кнопки сохраняют прежний URL. Проверка полного клиентского сценария на устройстве пока не подтверждена.

Туннель localhost.run временный: при смене адреса launcher восстанавливает маршрут; в этом запуске восстановление наблюдалось. Доступность требует работающих PyCharm-процесса, компьютера, Docker и сети. Сервер/постоянный домен не развёрнуты. Карго/оплата/старая база не подключались. Реальные уведомления клиентам не инициировались; перед запуском очередь была пустой.
