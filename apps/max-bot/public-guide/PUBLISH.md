# Инструкция: исходники и история

HTML сохранён как исходник PDF; публичная web-инструкция в baseline закрыта.
Файлы Caddyfile.before/proposed/closed — обезличенные исторические варианты, не план открытия сайта.
Актуальный runbook: [OPERATIONS](../../../docs/OPERATIONS.md).
Генерация PDF: из корня `npm ci`, `npx playwright install chromium`, `npm run guide:pdf`.
Результат — generated artifact под apps/max-bot/vpnmax/assets, не коммитится.
Загрузка PDF в MAX выдаёт secret reusable file token, который хранится только во внешнем env.
