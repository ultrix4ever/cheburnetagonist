# Upstream и права

## Amnezia Control

- https://github.com/MihSergeev/amnezia-control
- Baseline 0.84.6, pinned commit `7e0598b950b86f24dfdcff392bd5d8eccdb74f6e`.
- Upstream распространяется под AGPL-3.0; текст из pinned commit: `infra/panel/LICENSE.upstream`.
- Compose/nginx baseline происходит из upstream и адаптирован; panel-extension и build overlays
  интегрируются с его backend/frontend. Upstream copyright/license notices должны сохраняться.
- Полный upstream не включён: `scripts/fetch-panel-source.sh` получает фиксированный checkout.

## Остальные компоненты

FastAPI, HTTPX, Uvicorn, pytest, Playwright, Docker, Caddy, PostgreSQL и AmneziaWG имеют собственные
лицензии. Зависимости не становятся собственным кодом проекта. MAX CA — публичный сертификат
официального доверенного центра, используемый для проверки TLS.

Для авторского кода бота отдельная общая лицензия репозитория пока не выбрана владельцем.
Этот импорт не объявляет весь проект MIT/Apache и не меняет условий upstream.
