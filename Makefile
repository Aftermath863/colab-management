.DEFAULT_GOAL := help

SHELL := /usr/bin/env bash
SKIP_FRONTEND_BUILD ?= 0
APP_BUNDLE := dist/Colab Manager.app
DMG_IMAGE := dist/Colab-Manager-macOS-arm64.dmg

PYTHON ?= $(shell for py in $$(command -v python3 2>/dev/null) /opt/homebrew/Caskroom/miniconda/base/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do if [ -x "$$py" ] && "$$py" -c "import fastapi, pytest" >/dev/null 2>&1; then echo "$$py"; exit 0; fi; done; echo python3)

.PHONY: help macos-app macos-dmg macos-run macos-icon test test-backend test-frontend clean-macos

help: ## Show available targets
	@echo "Colab Management Platform — Available Targets:"
	@echo "  make macos-app       Build dist/Colab Manager.app (Apple Silicon arm64)"
	@echo "  make macos-dmg       Build dist/Colab-Manager-macOS-arm64.dmg installer"
	@echo "  make macos-run       Launch dist/Colab Manager.app (builds if missing)"
	@echo "  make macos-icon      Generate AppIcon.icns into dist/AppIcon.icns"
	@echo "  make test            Run backend pytest suite and frontend tests"
	@echo "  make test-backend    Run backend pytest suite"
	@echo "  make test-frontend   Run frontend test suite"
	@echo "  make clean-macos     Remove macos/.build and dist/"

macos-icon: ## Generate AppIcon.icns standalone
	@mkdir -p dist
	swift scripts/macos/generate_icon.swift dist/AppIcon.icns

macos-app: ## Build signed Apple Silicon .app bundle
	SKIP_FRONTEND_BUILD=$(SKIP_FRONTEND_BUILD) ./scripts/macos/build_app.sh

macos-dmg: macos-app ## Build distributable arm64 .dmg disk image
	./scripts/macos/build_dmg.sh

macos-run: ## Launch the macOS desktop app bundle (builds if missing)
	@if [ ! -d "$(APP_BUNDLE)" ]; then \
		SKIP_FRONTEND_BUILD=$(SKIP_FRONTEND_BUILD) ./scripts/macos/build_app.sh; \
	fi
	open "$(APP_BUNDLE)"

test-backend: ## Run backend pytest E2E suite
	OAUTHLIB_INSECURE_TRANSPORT=1 "$(PYTHON)" -m pytest backend/tests/ -v

test-frontend: ## Run frontend tests
	cd frontend && npm test

test: test-backend test-frontend ## Run full backend + frontend test suites

clean-macos: ## Clean macOS build and distribution artifacts
	rm -rf macos/.build dist
