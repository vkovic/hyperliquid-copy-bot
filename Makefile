.PHONY: help build up down logs restart clean test

help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Build Docker image
	docker-compose build

up: ## Start containers in detached mode
	docker-compose up -d

down: ## Stop and remove containers
	docker-compose down

logs: ## View logs (follow mode)
	docker-compose logs -f

logs-copier: ## View position copier logs
	docker-compose logs -f position-copier

restart: ## Restart all containers
	docker-compose restart

restart-copier: ## Restart position copier
	docker-compose restart position-copier

clean: ## Remove containers, images, and volumes
	docker-compose down -v
	docker rmi hyperliquid-app 2>/dev/null || true

test: ## Test Docker build
	./build-test.sh

ps: ## Show running containers
	docker-compose ps

exec: ## Open shell in position-copier container
	docker-compose exec position-copier /bin/bash

rebuild: ## Rebuild without cache and restart
	docker-compose down
	docker-compose build --no-cache
	docker-compose up -d

size: ## Show Docker image size
	docker images | grep hyperliquid

env: ## Show environment variables in container
	docker-compose exec position-copier env

.DEFAULT_GOAL := help

