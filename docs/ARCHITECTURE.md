# Архитектура

## Контуры и источники истины

| Контур | Истина | Что не хранит |
|---|---|---|
| MAX Registry | SQLite бота: люди, ownership, квоты, платежи, операции | client private keys, SSH credentials |
| Amnezia Control | PostgreSQL: ноды, admin/account/API settings; файлы панели | второй независимый MAX-реестр |
| VPN-нода | persistent config + live peers + clients metadata | доверенную идентичность человека по одному имени |
| MAX | numeric ID из события, доставки, webhook subscription | право на чужой профиль |
| Git | исходники и обезличенные проектные решения | рабочее состояние пользователей и secrets |

## Модули бота

- `api.py`: Panel и MaxAPI; TLS/CA/optional unix socket, контролируемые ошибки.
- `store.py`: SQLite/WAL, транзакции, quota/ownership, операции, payments/grants, recovery.
- `app.py`: FastAPI ingress, внутренний admin API и serial worker под `work_lock`.
- `bot.py`: команды, выдача/пауза/отзыв, замены, inventory и admin workflows.
- `registration.py`: контакт/ручной номер/имя; numeric MAX ID остаётся идентичностью.
- `billing.py`: цены/периоды, matching rules и не подключённые payment/fiscal stubs.
- `billing_bot.py`: диалог оплаты, прикрепление подтверждения, решения владельца, сроки.
- `reachability.py`: результаты внешней проверки доступности нод (`infra/rf-probe`): подтверждение
  двумя прогонами, отметки ✅/⛔ при выборе сервера, `/rf`; уведомлений не шлёт. Сам бот ноды не зондирует.
- `admin.py`: закрытый registry API; `cli.py`: обслуживание, не параллельная запись в production.

## Важные инварианты

1. Один процесс/worker. `asyncio.Lock` не является распределённой блокировкой.
2. Повтор webhook не должен повторять выдачу. Durable inbox подтверждается после сохранения.
3. После неоднозначного remote результата — `unknown`, не повтор POST автоматически.
4. Сетевая недоступность не превращает прежний инвентарь в missing.
5. `missing` не доказанный отзыв, блокировка бота не блокировка VPN.
6. Назначение профиля требует проверенного владельца; совпадения имён недостаточно.
7. Замена временно создаёт два профиля, но занимает один логический слот; отмена сохраняет старый.
8. Revocation требует согласованности реестра, persistent node config и live peers.
9. Full revoke сохраняет partial/unknown при неопределённом исходе; не обещает успех по тайм-ауту.
10. Конфиг доставляется только владельцу; не логировать export и не помещать его в Git.

## Панель и API

`max_awg3_v1.py` расширяет scoped API для AWG3, сохраняя server scopes и node locks upstream.
`max_users.py` делегирует registry admin requests через закрытую Docker-сеть; перед изменениями
проверяет штатный CurrentUser/step-up. React/CSS — отдельный раздел панели.
Клиент использует `/api/v1/servers/{id}/clients`, для `awg3` вставляет `/awg3`.
Смена upstream требует contract tests: названия handlers и сигнатуры — часть внешнего контракта.

## Сеть и процессы

На управляющем хосте: Caddy, frontend, backend, PostgreSQL и MAX-бот.
Ноды управляются SSH из backend; SSH host keys должны быть закреплены до смены маршрута.
Optional MAX relay — TCP-over-SSH unix forward, не HTTP proxy и не TLS termination.
Он имеет отдельного ограниченного пользователя и умеет reconnect; TLS всё равно проверяет бот.

Релиз бота, модификация панели и операция на VPN-ноде — разные единицы изменения.
Миграция управляющего хоста не требует смены endpoints существующих VPN-профилей.
При ней переносятся PostgreSQL, SQLite, panel SSH identity/known_hosts, TLS, env, webhook,
relay и backup, а не только frontend container.
