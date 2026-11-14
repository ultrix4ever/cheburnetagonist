# Перенос управляющих сервисов — 2026-11-14

Без изменения app/schema/engine управляющие сервисы перенесены с хоста существующей VPN-ноды
на отдельный manager. Прежняя VPN-нода продолжает работать с исходными endpoints и ключами.

## Что подтверждено

- Сохранены действующие образы; config digests транспортированного docker-save сверены после load.
  Source containerd store и target classic Docker могут показывать разные внешние image IDs:
  сравнивать нужно config digest, а не объявлять разные схемы ID разными сборками.
- PostgreSQL действительно восстановлен из финального custom dump; единственная SQLite перенесена
  после остановки старых writers. На исходных bot/backend/frontend/db отключён restart.
- Target bot/backend/db healthy; MAX API и subscriptions доступны, inventories четырёх нод проверены.
- Прежний VPN container ID, server config и client metadata на исходном хосте сохранены.
- Off-host backup перенаправлен на новый manager; SQLite restore из свежего архива прошёл integrity_check.
- HTTPS проверен на обоих edge IP с SNI/certificate verification: разрешённый источник получает UI 200,
  неразрешённый 403; webhook сохраняет method/secret policy. Поддельный XFF не обходит ACL.

## Временное состояние

До смены DNS прежний Caddy работает только как HTTPS proxy к target, не как второй backend.
Target доверяет client-IP headers только этому конкретному migration edge; после DNS-перехода
bridge и временное доверие удаляются. Реальные IP/host keys/доступы хранятся только в приватном runbook.

Новая co-hosted VPN-нода на manager — следующий отдельный этап: baseline подготовлен,
установка/регистрация и traffic lifecycle ещё не подтверждены. Admin authentication ждёт решения владельца;
это не повод выпускать JWT или менять пароль/2FA без разрешения.

UI глазами владельца не подтверждён. HTTP 200 и API checks не заменяют такой проверки.
