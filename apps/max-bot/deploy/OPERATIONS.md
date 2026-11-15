# Эксплуатация

Актуальный публичный runbook: [docs/OPERATIONS.md](../../../docs/OPERATIONS.md).
Развёртывание: [docs/DEPLOYMENT.md](../../../docs/DEPLOYMENT.md).
Файлы этого каталога — исходный operator toolkit, не безусловный production installer.

## Приём обновлений: webhook или опрос

По умолчанию MAX сам доставляет обновления на webhook. При `MAX_UPDATES=poll` бот сам забирает их
длинным опросом через тот же relay, что и исходящие вызовы; подписка webhook при этом должна быть
снята, иначе MAX опрос не обслуживает.

- Переход на опрос: записать текущий URL подписки, выполнить в контейнере `updates_mode.py poll`,
  добавить `MAX_UPDATES=poll` в env-файл бота, пересоздать только контейнер бота.
- Возврат: убрать `MAX_UPDATES`, пересоздать контейнер, выполнить `updates_mode.py webhook`
  с `WEBHOOK_URL` равным сохранённому URL.
- `/health` в режиме опроса отвечает 503 после десяти неудачных опросов подряд.
- `updates_mode.py` в образ не входит: `docker exec -i <bot> python - poll < updates_mode.py`.
