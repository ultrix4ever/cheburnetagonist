# Эксплуатация

## Быстрая проверка

На manager проверить Compose status/health без вывода `.Config.Env` и env-файлов.
Из bot container: GET `/me` и `/subscriptions` через MaxAPI, только коды/наличие ожидаемого URL.
Проверить свежий инвентарь каждой ноды, а не только кэш online.
В SQLite read-only: integrity, размеры очередей, pending/unknown, ready replacements,
revocation backlog и возраст последней успешной синхронизации. Не печатать inbox body/export.

Ожидаемая внешняя политика: panel/API без allowlist → 403; закрытые /guide варианты → 404;
webhook GET → 405, POST без секрета → 403. Не открывать всю панель ради проверки health.

## Неопределённые операции

Не повторять issue/revoke после сетевого тайм-аута до сверки. Проверить durable intent,
корреляционную метку, panel inventory и persistent/live peers. Отдельно записать доказательство
и исход; только затем reconcile. Не присваивать владельца по имени старого профиля.

## Backup

Текущая реализация: `infra/backup/backup.py`, timer ежедневно 02:20 UTC + jitter.
Содержит PostgreSQL custom dump, SQLite через backup API, env и registry-admin secret,
panel files/SSH identity, release-v2 source/build context. Хранить только в защищённом контуре.
IP/пути в публичном шаблоне требуют адаптации; это не исполняемый план для live инфраструктуры.

После каждой миграции:

1. Проверить код завершения, свежесть/размер архива и права 0600.
2. В закрытом temp-каталоге восстановить SQLite, `PRAGMA integrity_check`.
3. В изолированной БД восстановить PostgreSQL и проверить связи/миграции.
4. Сверить комплектность panel SSH/config/env, не запуская второй production worker.
5. Удалить временные секретные файлы, сохранить только обезличенный результат.

Автоматической ротации и доказанного полного DR PostgreSQL в исходном baseline нет.
Backup на Gateway — off-host относительно панели, но не гарантия независимого географического хранения.

## Релизы и откат

- Перед релизом fresh backup и immutable image IDs; изменения бота отдельно от panel/нод.
- Не возвращать старую SQLite поверх новых оплат/операций/ownership.
- Откат старого кода может потерять enforcement новых правил даже при совместимой схеме.
- Не запускать второй бот/registry; при откате сначала остановить новую копию.
- Не перезапускать VPN-контейнеры при замене UI/бота.

## Типовые сбои

| Симптом | Первичная проверка |
|---|---|
| MAX молчит, VPN работает | relay service/socket/permissions, TLS CA, `/me`, inbox |
| Одна нода 502 | backend SSH route/ACL/host key, повторное read-only чтение; не удалять старые profiles |
| Сайт 403 | ожидаемый ACL, не обязательно сбой |
| Лимит неожиданно занят | missing/paused/замена/unknown, сроки grants; не освобождать слот по timeout |
| Чек получен, слоты не выданы | статус awaiting/submitted, ручное решение владельца |
| Бот blocked, VPN работает | это штатное различие bot-only block и revoke-all |
| Backup success, восстановление падает | валидировать restore, а не только exit code/наличие tgz |
