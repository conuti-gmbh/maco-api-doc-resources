# Local dev wrapper around the tooling image (docker/dev/Dockerfile) so devs need
# nothing installed but Docker — same Python + vacuum versions as CI.
#
#   make build   # build the dev image (once / after dep or vacuum bumps)
#   make test    # pytest scripts/tests
#   make lint    # vacuum-lint the spec dirs that exist (pruefi/ event/ … bundle/)
#   make refs    # $ref consistency check
#   make shell   # interactive shell in the image
#
# The full sync (templater + translator + process repos) is NOT here — that is
# multi-runtime/cross-repo and runs in .github/workflows/sync.yaml.

IMAGE := maco-api-doc-resources-dev
RUN   := docker run --rm -v "$(CURDIR)":/work -w /work $(IMAGE)

.PHONY: build test lint refs shell help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  %-8s %s\n", $$1, $$2}'

build: ## Build the dev/tooling image
	docker build -f docker/dev/Dockerfile -t $(IMAGE) .

test: ## Run the generator test suite (pytest)
	$(RUN) python -m pytest scripts/tests

lint: ## OpenAPI 3.1 lint over the spec dirs present (and bundles)
	$(RUN) bash scripts/lint-openapi.sh

refs: ## Check external $ref consistency
	$(RUN) python -m scripts.check_refs

shell: ## Interactive shell in the dev image
	docker run --rm -it -v "$(CURDIR)":/work -w /work $(IMAGE) bash
