# Секреты и границы доверия

- Не публиковать .env, SSH/private VPN keys, реестры, dumps, чеки, номера телефонов,
  invite codes, reusable file tokens и полные ответы export API.
- В примерах только RFC 5737 IP и example.net. public root CA в certs — не private key.
- Не включать рабочие токены в CI, pull requests или скриншоты. CI работает offline.
- Только проверенный numeric messenger ID задаёт владельца. Имя/телефон не доказывают ownership.
- Внешние webhook события недоверенные; обязательны secret, дедупликация и повторная проверка прав.
- API-ключ панели ограничить нодами. Registry secret отделён от него и от MAX token.
- `unknown`, `missing`, blocked и revoked — разные состояния. Не подтверждать отзыв по timeout.
- Не печатать сырые HTTP errors/logs/acceptance stdout: они могут содержать конфиг.
- Уязвимость сообщать владельцу приватно, не публиковать рабочие credentials в issue.

Git — не backup production и не secret manager. Найденный секрет сначала отзывается/ротируется;
удаление текущего файла не очищает историю Git.
