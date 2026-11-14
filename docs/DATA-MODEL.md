# Модель данных и состояния

Схема сейчас создаётся/расширяется `Store` при старте; отдельного versioned migration framework нет.
До изменений схемы нужен SQLite backup API snapshot; файл с активным WAL не копировать как единственный backup.

| Таблица | Назначение |
|---|---|
| people | numeric MAX ID, имя, телефон/verification metadata, active, quota, allowed_servers, billing flags |
| profiles | server/protocol/public key identity, name, owner, state, export availability и billing linkage |
| invites / confirmations | хеши одноразовых кодов, ожидаемый владелец, TTL, replay protection |
| operations | durable intent и результат изменения VPN; pending/done/failed/unknown |
| replacements | old/new profile, mode, deadline и creating/ready/committing/canceling/unknown/terminal state |
| inbox | дедупликация входящих событий, pending/processing/done/interrupted |
| admin_jobs | durable полный отзыв, итог done/partial/unknown |
| revocations | очередь подтверждаемого отзыва с попытками и результатом |
| payments / grants | запросы оплаты, ручное решение, пакеты оплаченных слотов и сроки |
| prompts | временный контекст диалога |
| server_catalog | кэш видимых серверов, online и timestamp; не заменяет свежий client inventory |
| audit | журнал действий; без конфигов и private keys |

## Не смешивать состояния

- **active/paused**: известный профиль; пауза не означает освобождение слота автоматически.
- **missing**: ранее известный профиль не найден в успешном inventory, но нет подтверждённого revoke.
- **revoked**: подтверждённый жизненный цикл отзыва; при восстановлении старого node backup профиль может всплыть.
- **unknown**: невозможно доказать результат мутации. Требуется операторская сверка, не replay.
- **interrupted**: событие было processing при recovery; тело очищается, действие слепо не повторяется.
- **awaiting/submitted**: платеж ещё не подтверждён; PDF/скриншот сам по себе не создаёт grant.

Для расследования unknown: correlation label → фактические profiles/live peers → persistent config →
принадлежность/квота → вручную зафиксированный вывод. Не меняйте только status в SQLite ради зелёного UI.
CLI reconcile закрывает запись, но сам не доказывает состояние ноды.
