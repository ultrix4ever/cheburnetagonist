# Roadmap модернизации

Статус: предложения и критерии приёмки, не обещание сроков. Разрешение на подготовку Git
не означает автоматический запуск всех feature/инфраструктурных этапов.

## 0 — Baseline и единый репозиторий (P0)

- [x] Импортировать app/тесты/panel extensions/ops без live данных.
- [x] Зафиксировать зависимости, архитектуру, provenance и границы проверки.
- [x] Отделить MAX roadmap от корпоративного Pachca draft.
- [x] Первый удалённый CI успешен: baseline commit d6fc6ac, workflow run 43590070836.

Готово: чистый clone, offline tests, секрет-скан и документация воспроизводимы.

## 1 — Воспроизводимая сборка (P0)

- Python transitive lock и digest базового image; контролируемая процедура обновления.
- Единый release manifest: bot/backend/frontend/upstream/schema/config compatibility.
- Убрать приватные absolute paths и one-off deployment scripts из штатного пути релиза.
- Pinned upstream contract CI без skipped; reproducible frontend/PDF build.
- Staging с отдельными токенами/базой/node scopes и проверкой rollback.

Приёмка: из чистого clone собираются одинаково идентифицируемые artifacts, contract suite не skipped,
проверены upgrade/rollback на копии данных без подключения к production bot.

## 2 — Надёжная эксплуатация (P0)

- Разделить liveness и readiness; метрики очереди, age inventory, неизвестных операций,
  relay reconnect, ошибок MAX/нод, backup freshness.
- Structured redacted logs и correlation IDs без user content/keys.
- Backup retention по согласованному сроку; дополнительная независимая копия.
- Полный PostgreSQL+SQLite restore drill в isolated environment.

Сделано: перенос manager на новый хост с одним writer, переключением DNS и уборкой временного
proxy; проверка доступности нод настоящим VPN-клиентом из сети с ограничениями
(`infra/rf-probe`), отметки при выборе сервера в боте; воспроизводимый релиз бота из commit
(`deploy/release.py`). Дальше: вторая точка у мобильного или домашнего провайдера,
отображение в панели, история доступности.

Приёмка: искусственные сбои видны, алерт не содержит секретов, восстановление доказывает
сохранность владельцев/платежей/панели, production VPN-клиенты не затрагиваются.

## 3 — Безопасное изменение состояния (P1)

- Поддерживаемая ОС relay; bounded maintenance с откатом.
- Ротация/сроки panel API, relay и node SSH keys; отдельный журнал доступа.
- Явные версионированные schema migrations и checks перед стартом.
- Операторский recovery UI для unknown: показывать доказательства, не blind retry.
- Упорядочить журнал, hard-delete и retention чувствительных данных.

Приёмка: повторы/краши/сеть не создают двойные ключи, не теряют ownership и не выдают ложный revoke success.

## 4 — UX личного MAX-продукта (P1)

- Понятная саморегистрация и статусы ручного номера/доступа/оплаты.
- Отображение лимита и срока пакетов без смешения профиля, устройства и оплаченного слота.
- Надёжная доставка PDF/конфига, обработка недоступного reusable file token.
- Отдельный сценарий поддержки: задержанная доставка, missing, relay offline, незавершённая замена.
- Обновить acceptance: owner isolation, full quota, replacement expiry, billing expiry, receipt retries,
  pause/resume/revoke и восстановление исходного live/persistent peer baseline.

Приёмка: сценарии проходят для двух изолированных пользователей; есть отдельная live-проверка
импорта в клиент и реального трафика, без приписывания её результатам offline suite.

## 5 — Платёжная автоматизация (P2, отдельное решение)

- Выбрать официальный provider/feed и способ фискализации; не scrape банковское приложение.
- Sandbox adapter с provider operation ID, exact amount/code matching и дедупликацией.
- Несовпадения и спорные платежи — в ручную очередь; replay не добавляет grant второй раз.
- Ручное подтверждение остаётся рабочим fallback.

Приёмка: duplicate/out-of-order/ошибка суммы/отзыв платежа покрыты; audit не содержит банковских секретов.
Никаких платных подключений без выбранных условий владельцем.

## 6 — Необязательный корпоративный Pachca-контур (P2)

Отдельный продукт, не замена текущему MAX. [Разбор исходного draft](ROADMAP-REVIEW.md).
До кода выбрать: purpose/nodes, approvers, hosting, quota/TTL, пространства, аудит и offboarding.
Свой bot token/API key/SQLite/container; личные пользователи и платежи MAX не смешиваются с сотрудниками.

Приёмка: запрещены самоодобрение, replay, чужой callback и доступ suspended user;
approval→issue→revoke подтверждён трафиком; работа MAX до/после не изменилась.

## 7 — Масштабирование по измерениям (позже)

Capacity и ресурсные лимиты нод; нагрузочный стенд; выбор координации задач и распределённых locks.
PostgreSQL для registry/очередь/HA рассматривать только после измерения потребности и схемы транзакций.
`replicas: 2` или несколько Uvicorn workers сейчас не являются корректным масштабированием.

## Закрытые инфраструктурные задачи

- Перенос главной панели и MAX-бота на другой manager; прежняя VPN-нода сохранена с теми же ключами.
- Новая co-hosted VPN-нода на manager: добавлена в панель и проверена реальным трафиком.
  В список серверов бота она не включена — это отдельное решение владельца.
