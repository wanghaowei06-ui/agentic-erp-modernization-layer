.PHONY: install test smoke start reset docker-up docker-down

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	chmod +x scripts/start_all.sh scripts/smoke_test.sh scripts/reset_demo_data.sh scripts/ci_test.sh

test:
	.venv/bin/python -m pytest mock-legacy-erp reasoning-agent generated-api-facade validation-suite

smoke:
	./scripts/smoke_test.sh

start:
	./scripts/start_all.sh

reset:
	./scripts/reset_demo_data.sh

docker-up:
	docker compose up --build

docker-down:
	docker compose down
