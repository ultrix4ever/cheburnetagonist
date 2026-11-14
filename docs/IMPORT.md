# Происхождение импорта

Дата: 2026-11-14. Источники: переданный владельцем локальный проект VPN MAX,
его test suite, panel overlays, Compose baseline и operator tools.

Сохранён полный доступный прикладной код бота, расширений и тестов, build/acceptance/deployment
scripts, HTML-источник инструкции, relay и текущий backup code.
Полный upstream панели/движка не вендорится; fixed revision доступен отдельно.

## Изменения при публикации

- Код размещён в apps/max-bot, infrastructure в infra, исторические tools отдельно.
- Реальные IP/домены/MAX IDs/частные absolute paths в текстовых deployment материалах заменены
  документальными значениями. Рабочие secrets не переносились в staging repository.
- Абсолютные импорты Playwright заменены normal package import, добавлен Node manifest/lock.
- Рабочий PDF — generated artifact; в Git публикуется его обезличенный HTML source,
  генератор создаёт PDF локально. reusable MAX file token всегда вне Git.
- Исторические docs с личными деталями заменены публичными обзорами; приватные originals сохранены вне Git.
- Добавлены README, архитектура, state model, roadmap/review, knowledge index, CI и правила вклада.

## Намеренно исключено

Рабочие .env/DB/WAL/dumps/backups, SSH keys/authorized_keys/known_hosts,
сертификат конкретной панели, чеки и вложения пользователей, screenshots с live данными,
raw agent memory и приватный корпоративный roadmap. Публичный корневой CA MAX оставлен.

Это публикация исходников, не резервное копирование production и не развёртывание новой версии.
