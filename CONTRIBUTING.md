# Разработка

1. Читать README, docs/ARCHITECTURE.md, docs/DECISIONS.md и docs/ROADMAP.md.
2. Делать небольшие изменения с критериями приёмки; сохранять single-worker/ownership/unknown semantics.
3. `make test`; для изменения panel adapter — также pinned upstream contract tests.
4. Для UI — upstream build, mock browser checks, затем отдельная live-проверка при разрешённом доступе.
5. Изменения схемы требуют backup/миграции/совместимости; не заменять production SQLite локальной.
6. Перед commit: gitleaks и проверка diff. Не импортировать raw production snapshots.
7. Обновлять docs вместе с решением. Не помечать будущую функцию реализованной.

Ветки: небольшие feature/fix/docs ветки, review/CI перед merge. Git не запускает автодеплой.
Live acceptance может создавать профили/контейнеры: только отдельный тестовый scope,
проверенный baseline, очистка и доказательство восстановления исходных peers.
