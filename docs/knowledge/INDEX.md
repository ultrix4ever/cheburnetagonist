# Индекс знаний

## Перед работой

1. [README](../../README.md): scope, стек и текущая реализация.
2. [Архитектура](../ARCHITECTURE.md) и [состояния](../DATA-MODEL.md): инварианты.
3. [Решения](../DECISIONS.md) и [roadmap](../ROADMAP.md): принятое против предложенного.
4. [Операции](../OPERATIONS.md) / [deployment](../DEPLOYMENT.md): live изменения отдельно от Git.
5. [Baseline](BASELINE-2026-11-14.md): что действительно проверялось.
6. [Management cutover](MANAGEMENT-CUTOVER-2026-11-14.md): проверенный перенос и оставшиеся шаги.

## Где код

- App и schema: `apps/max-bot/vpnmax/`.
- Panel adapter/React: `apps/max-bot/panel-extension/`.
- Failure/ownership/billing tests: `apps/max-bot/tests/`.
- Приватные one-off operator scripts импортированы обезличенно в deploy/tools, не являются штатным pipeline.

## Приватный операционный индекс

В рабочем Gateway отдельно хранятся live HANDOVER, current-state audit, topology и доступы.
В публичном репозитории нет ссылок на приватные URL/секреты и нет raw памяти агента.
При изменении проекта обновлять публичное решение и приватный runbook раздельно.
