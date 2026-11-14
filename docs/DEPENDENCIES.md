# Зависимости и воспроизводимость

## Зафиксированный baseline

- Python direct pins: `apps/max-bot/requirements.txt`; pytest: `requirements-dev.txt`.
- Browser tooling: `package.json` и `package-lock.json`; production Python не требует Node.js.
- Upstream panel commit: `7e0598b950b86f24dfdcff392bd5d8eccdb74f6e`.
- Backend base digest: `sha256:8e850c85cf57d5ed790c45194a1af28a81dbd99dd51854933100c178d2aa0564`.
- Frontend base digest: `sha256:426750a4972184e787d93e9311f221cfab14ca2cb2370eb5d81fa2bac42ff9e0`.
- Исходный Caddy digest находится в `infra/panel/compose.edge.yml`.
- Полный dependency/SBOM audit этим импортом не выполнен; fixed version не доказывает отсутствие CVE.

## Разделение окружений

Runtime бота: FastAPI/HTTPX/Uvicorn. Pytest — только development.
Upstream backend contract tests дополнительно импортируют SQLAlchemy/settings/auth и другие
зависимости pinned панели: устанавливать их в отдельное окружение по upstream manifest.
TypeScript/Vite build делается в подготовленном upstream frontend: здесь нет копии всего React приложения.
Playwright применяется к полученному dist; без dist UI-тест нельзя назвать пройденным.

Dockerfile бота основан на `python:3.12-slim`, пока без digest; это известная задача P0.
Не выполнять бездумный update всех зависимостей и панели в одном релизе.

## Внешние контракты

- MAX: `https://platform-api2.max.ru`, numeric ID, webhook secret и file upload token.
- Panel: scoped `X-API-Key`, отдельные awg/awg3 routes, JWT/step-up для администрирования.
- SSH: panel key на нодах; relay key отдельно. Смена источника требует сверки from=/ACL/known_hosts.
- Backup: Python stdlib + ssh + docker exec + pg_dump; нужен доступ к обоим источникам данных.
- Дополнительные CA: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt

## План обновлений

Отдельные PR: Python dependencies → panel contract → frontend build → bot staging →
live disposable VPN acceptance → deployment/rollback evidence. Раздельно обновлять ОС relay и VPN engine.
