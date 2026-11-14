# Развёртывание и перенос

## Что является шаблоном

`infra/panel/` — sanitized исходный Compose baseline, не полная текущая установка бота.
`apps/max-bot/compose.example.yml` — merge-template; для v2 нужен **дополнительный** registry-admin env_file
у backend и бота, а также производные backend/frontend образы. Старт одного шаблона не даёт готовую систему.
Расположение исходного production: manager `/opt/amnezia-control`, bot `/opt/vpn-max`.
На новой установке пути можно выбрать иначе, но корректировать manifests согласованно.

## Build

1. Получить pinned upstream через `scripts/fetch-panel-source.sh`.
2. `python3 apps/max-bot/deploy/prepare_v2.py .cache/upstream/amnezia-control .cache/build/panel`.
3. В `.cache/build/panel/frontend`: `npm ci && npm run build`; dist скопировать в frontend-build.
4. Собрать производные backend/frontend; сохранить digests и version manifest.
5. Бот: `docker build -t vpn-max:<release> apps/max-bot` после тестов.
6. PDF строится отдельно из HTML и загружается в MAX; в runtime используется GUIDE_FILE_TOKEN,
   сам PDF не является автоматической частью регистрации webhook.

Не публиковать build contexts с .env/dumps. Верхний `.dockerignore` бота исключает runtime files.

## Перед переключением

- Согласованный PostgreSQL dump, SQLite backup API, panel data/SSH identity/known_hosts,
  env, registry admin secret, TLS certificates, текущие compose и image IDs.
- Инвентарь нод и работающих контейнеров; список endpoints/ports и сетевых ACL.
- Проверенный rollback: кто single writer, что остановить перед включением прежней копии.
- Не менять одновременно схему данных, версию панели, VPN engine и хост.

## Cutover управляющего хоста

1. Подготовить target, контейнерные сети и storage; не запускать второй рабочий bot/worker.
2. Предварительная копия и проверка ресурсов/образов/host keys.
3. Добавить новый source IP к ограничениям panel SSH ключей на нодах, сохраняя старый на время отката.
4. Остановить исходные пишущие сервисы, сделать финальные дампы и восстановить их на target.
5. Перенести relay endpoint, сертификаты, network ACL, webhook route и backup destination.
6. Запустить ровно один bot/registry, проверить `/me`, subscriptions, внутренний API и все ноды.
7. Переключить DNS/edge; при необходимости временный proxy к target обеспечивает переход без двух writers.
8. Проверить HTTPS из внешнего/разрешённого источника, UI, регистрацию/owner scope,
   backup/restore и независимость от выключенных исходных management containers.
9. Исходные VPN-контейнеры можно оставить на прежнем хосте: не менять endpoint клиентов только ради панели.

Новая co-hosted Amnezia-нода — отдельная операция: уникальный ID/порт/подсеть, pinned host key,
resource limits, backup и disposable real-traffic test с подтверждённой очисткой.
Не использовать Adopt/перезапуск существующего контейнера как способ добавить новую ноду.

## Что не доказывает успех

HTTP 200 главной не доказывает работающий node API, MAX или пользовательский VPN.
Panel dispatch не доказывает UI владельца. Health без очередей/relay — только liveness.
Состав backup не доказывает восстановление PostgreSQL; отдельный restore drill необходим.
