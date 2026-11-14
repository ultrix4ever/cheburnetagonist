PYTHON ?= $(CURDIR)/.venv/bin/python

.PHONY: test compile secrets
test:
	cd apps/max-bot && $(PYTHON) -m pytest -q

compile:
	$(PYTHON) -m compileall -q apps/max-bot/vpnmax apps/max-bot/panel-extension apps/max-bot/deploy infra/backup

secrets:
	gitleaks dir . --redact --no-banner
