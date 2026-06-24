.PHONY: install test smoke evidence demo-check start reset docker-up docker-down

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	chmod +x scripts/dev-start.sh scripts/dev-stop.sh scripts/smoke-test.sh scripts/start_all.sh scripts/smoke_test.sh scripts/reset_demo_data.sh scripts/ci_test.sh scripts/collect-demo-evidence.sh

test:
	.venv/bin/python -m pytest shared/automation_memory/tests reasoning-agent validation-suite generated-api-facade mock-legacy-erp

smoke:
	./scripts/smoke-test.sh

evidence:
	./scripts/collect-demo-evidence.sh

demo-check: test smoke evidence

start:
	./scripts/dev-start.sh

reset:
	./scripts/reset_demo_data.sh

docker-up:
	docker compose up --build

docker-down:
	docker compose down
