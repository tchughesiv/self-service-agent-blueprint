# Makefile for RAG Deployment
ifeq ($(NAMESPACE),)
ifneq (,$(filter namespace helm-install-dev helm-install-test helm-install-prod helm-uninstall helm-status helm-cleanup-eventing helm-cleanup-jobs,$(MAKECMDGOALS)))
$(error NAMESPACE is not set)
endif
endif

VERSION ?= 0.0.2
CONTAINER_TOOL ?= podman
REGISTRY ?= quay.io/ecosystem-appeng
PYTHON_VERSION ?= 3.12
ARCH ?= linux/amd64
AGENT_IMG ?= $(REGISTRY)/self-service-agent:$(VERSION)
REQUEST_MGR_IMG ?= $(REGISTRY)/self-service-agent-request-manager:$(VERSION)
AGENT_SERVICE_IMG ?= $(REGISTRY)/self-service-agent-service:$(VERSION)
INTEGRATION_DISPATCHER_IMG ?= $(REGISTRY)/self-service-agent-integration-dispatcher:$(VERSION)
MCP_SNOW_IMG ?= $(REGISTRY)/self-service-agent-snow-mcp:$(VERSION)
MOCK_EVENTING_IMG ?= $(REGISTRY)/self-service-agent-mock-eventing:$(VERSION)

MAKFLAGS += --no-print-directory

# Default values
POSTGRES_USER ?= postgres
POSTGRES_PASSWORD ?= rag_password
POSTGRES_DBNAME ?= rag_blueprint

# HF_TOKEN is only required if LLM_URL is not set
ifeq ($(LLM_URL),)
HF_TOKEN ?= $(shell bash -c 'read -r -p "Enter Hugging Face Token: " HF_TOKEN; echo $$HF_TOKEN')
else
HF_TOKEN ?=
endif

MAIN_CHART_NAME := self-service-agent
TOLERATIONS_TEMPLATE=[{"key":"$(1)","effect":"NoSchedule","operator":"Exists"}]
INGRESS_PREFIX := ssa

# Slack Configuration - only when ENABLE_SLACK set to true
ifeq ($(ENABLE_SLACK),true)
ifndef SLACK_BOT_TOKEN
SLACK_BOT_TOKEN := $(shell bash -c 'read -r -p "Enter Slack Bot Token (xoxb-...): " TOKEN; echo $$TOKEN')
endif
ifndef SLACK_SIGNING_SECRET
SLACK_SIGNING_SECRET := $(shell bash -c 'read -r -p "Enter Slack Signing Secret: " SECRET; echo $$SECRET')
endif
endif

# Check if Slack should be enabled
SLACK_ENABLED := $(if $(and $(SLACK_BOT_TOKEN),$(SLACK_SIGNING_SECRET)),true,false)

# ServiceNow Configuration
# Can be set either way:
#   1. Environment variable (required for passwords with $ or other special chars):
#      export SERVICENOW_PASSWORD='P@ssw0rd$r123'
#      make helm-install-dev ...
#   2. Make argument (for simple passwords without special chars):
#      make helm-install-dev SERVICENOW_PASSWORD=simple123 ...
SERVICENOW_INSTANCE_URL ?=
SERVICENOW_USERNAME ?=
SERVICENOW_PASSWORD ?=
SERVICENOW_AUTH_TYPE ?= basic
USE_REAL_SERVICENOW ?= false

# Export to shell so kubectl can access them
export SERVICENOW_INSTANCE_URL
export SERVICENOW_USERNAME
export SERVICENOW_PASSWORD

helm_pgvector_args = \
    --set pgvector.secret.user=$(POSTGRES_USER) \
    --set pgvector.secret.password=$(POSTGRES_PASSWORD) \
    --set pgvector.secret.dbname=$(POSTGRES_DBNAME)

helm_llm_service_args = \
    $(if $(HF_TOKEN),--set llm-service.secret.hf_token=$(HF_TOKEN),) \
    $(if $(LLM),--set global.models.$(LLM).enabled=true,) \
    $(if $(SAFETY),--set global.models.$(SAFETY).enabled=true,) \
    $(if $(LLM_TOLERATION),--set-json global.models.$(LLM).tolerations='$(call TOLERATIONS_TEMPLATE,$(LLM_TOLERATION))',) \
    $(if $(SAFETY_TOLERATION),--set-json global.models.$(SAFETY).tolerations='$(call TOLERATIONS_TEMPLATE,$(SAFETY_TOLERATION))',) \
    $(if $(LLM_URL),--set llm-service.enabled=false,)

helm_llama_stack_args = \
    $(if $(LLM),--set global.models.$(LLM).enabled=true,) \
    $(if $(SAFETY),--set global.models.$(SAFETY).enabled=true,) \
    $(if $(LLM_URL),--set global.models.$(LLM).url='$(LLM_URL)',) \
    $(if $(LLM_ID),--set global.models.$(LLM).id='$(LLM_ID)',) \
    $(if $(SAFETY_URL),--set global.models.$(SAFETY).url='$(SAFETY_URL)',) \
    $(if $(LLM_API_TOKEN),--set global.models.$(LLM).apiToken='$(LLM_API_TOKEN)',) \
    $(if $(SAFETY_API_TOKEN),--set global.models.$(SAFETY).apiToken='$(SAFETY_API_TOKEN)',) \
    $(if $(LLAMA_STACK_ENV),--set-json llama-stack.secrets='$(LLAMA_STACK_ENV)',)

helm_request_management_args = \
    $(if $(REQUEST_MANAGEMENT),--set requestManagement.enabled=$(REQUEST_MANAGEMENT),) \
    $(if $(KNATIVE_EVENTING),--set requestManagement.knative.eventing.enabled=$(KNATIVE_EVENTING),) \
    $(if $(MOCK_EVENTING),--set requestManagement.knative.mockEventing.enabled=$(MOCK_EVENTING),) \
    $(if $(SLACK_SIGNING_SECRET),--set-string security.slack.signingSecret='$(SLACK_SIGNING_SECRET)',) \
    $(if $(SNOW_API_KEY),--set-string security.apiKeys.snowIntegration='$(SNOW_API_KEY)',) \
    $(if $(HR_API_KEY),--set-string security.apiKeys.hrSystem='$(HR_API_KEY)',)

# Version target
.PHONY: version
version:
	@echo $(VERSION)

# Default target
.PHONY: help
help:
	@echo "Available targets:"
	@echo ""
	@echo "Build Commands:"
	@echo "  build-all-images                     - Build all container images (checks lockfiles first)"
	@echo "  build-agent-image                    - Build the unified self-service agent container image (checks lockfiles first)"
	@echo "  build-agent-service-image            - Build the agent service container image (checks lockfiles first)"
	@echo "  build-integration-dispatcher-image   - Build the integration dispatcher container image (checks lockfiles first)"
	@echo "  build-mcp-snow-image                 - Build the snow MCP server container image (checks lockfiles first)"
	@echo "  build-mock-eventing-image            - Build the mock eventing service container image (checks lockfiles first)"
	@echo "  build-request-mgr-image              - Build the request manager container image (checks lockfiles first)"
	@echo ""
	@echo "Helm Commands:"
	@echo "  helm-install-dev                    - Install with direct HTTP communication (development)"
	@echo "  helm-install-test                   - Install with mock eventing service (testing/CI)"
	@echo "  helm-install-prod                   - Install with full Knative eventing (production)"
	@echo "  helm-cleanup-eventing               - Manually clean up leftover Knative Eventing resources (Triggers, Brokers)"
	@echo "  helm-cleanup-jobs                   - Clean up leftover jobs from failed deployments"
	@echo "  helm-depend                         - Update Helm dependencies"
	@echo "  helm-list-models                    - List available models"
	@echo "  helm-status                         - Check status of the deployment"
	@echo "  helm-uninstall                      - Uninstall the RAG deployment and clean up resources"
	@echo "  verify-triggers                     - Verify all expected Knative triggers are deployed"
	@echo ""
	@echo "Install Commands:"
	@echo "  install-all                         - Install dependencies for all projects"
	@echo "  install                             - Install dependencies for self-service agent"
	@echo "  install-agent-service               - Install dependencies for agent service"
	@echo "  install-asset-manager               - Install dependencies for asset manager"
	@echo "  install-integration-dispatcher      - Install dependencies for integration dispatcher"
	@echo "  install-mcp-snow                    - Install dependencies for snow MCP server"
	@echo "  install-request-manager             - Install dependencies for request manager"
	@echo "  install-shared-models               - Install dependencies for shared models"
	@echo "  install-shared-clients              - Install dependencies for shared clients"
	@echo ""
	@echo "Reinstall Commands:"
	@echo "  reinstall-all                       - Force reinstall dependencies for all projects (uv sync --reinstall)"
	@echo "  reinstall                           - Force reinstall self-service agent dependencies (uv sync --reinstall)"
	@echo "  reinstall-agent-service             - Force reinstall agent service dependencies"
	@echo "  reinstall-asset-manager             - Force reinstall asset manager dependencies"
	@echo "  reinstall-integration-dispatcher    - Force reinstall integration dispatcher dependencies"
	@echo "  reinstall-mcp-snow                  - Force reinstall snow MCP dependencies"
	@echo "  reinstall-request-manager           - Force reinstall request manager dependencies"
	@echo "  reinstall-shared-models             - Force reinstall shared models dependencies"
	@echo "  reinstall-shared-clients            - Force reinstall shared clients dependencies"
	@echo ""
	@echo "Push Commands:"
	@echo "  push-all-images                     - Push all container images to registry"
	@echo "  push-agent-image                    - Push the unified self-service agent container image to registry"
	@echo "  push-agent-service-image            - Push the agent service container image to registry"
	@echo "  push-integration-dispatcher-image   - Push the integration dispatcher container image to registry"
	@echo "  push-mcp-snow-image                 - Push the snow MCP server container image to registry"
	@echo "  push-mock-eventing-image            - Push the mock eventing service container image to registry"
	@echo "  push-request-mgr-image              - Push the request manager container image to registry"
	@echo ""
	@echo "Test Commands:"
	@echo "  test-all                            - Run tests for all projects"
	@echo "  test                                - Run tests for self-service agent"
	@echo ""
	@echo "Lockfile Management:"
	@echo "  check-lockfiles                     - Check if all uv.lock files are up-to-date"
	@echo "  update-lockfiles                    - Update all uv.lock files to match pyproject.toml"
	@echo "  check-lockfile-<service>            - Check lockfile for specific service"
	@echo "  update-lockfile-<service>           - Update lockfile for specific service"
	@echo "  test-agent-service                  - Run tests for agent service"
	@echo "  test-asset-manager                  - Run tests for asset manager"
	@echo "  test-integration-dispatcher         - Run tests for integration dispatcher"
	@echo "  test-mcp-snow                       - Run tests for snow MCP server"
	@echo "  test-request-manager                - Run tests for request manager"
	@echo "  test-shared-models                  - Run tests for shared models"
	@echo "  test-short-integration-request-mgr  - Run short integration tests with Request Manager"
	@echo "  test-short-resp-integration-request-mgr - Run short responses integration tests with Request Manager (no employee ID)"
	@echo ""
	@echo "Utility Commands:"
	@echo "  format                              - Run isort import sorting and Black formatting on entire codebase"
	@echo "  lint                                - Run flake8 linting on entire codebase"
	@echo "  version                             - Print the current VERSION"
	@echo ""
	@echo "Configuration options (set via environment variables or make arguments):"
	@echo ""
	@echo "  Core Configuration:"
	@echo "    CONTAINER_TOOL                    - Container build tool (default: podman)"
	@echo "    REGISTRY                          - Container registry (default: quay.io/ecosystem-appeng)"
	@echo "    VERSION                           - Image version tag (default: 0.0.2)"
	@echo "    NAMESPACE                         - Target namespace (required, no default)"
	@echo ""
	@echo "  Image Configuration:"
	@echo "    AGENT_IMG                         - Full agent image name (default: \$${REGISTRY}/self-service-agent:\$${VERSION})"
	@echo "    AGENT_SERVICE_IMG                 - Full agent service image name (default: \$${REGISTRY}/self-service-agent-service:\$${VERSION})"
	@echo "    INTEGRATION_DISPATCHER_IMG        - Full integration dispatcher image name (default: \$${REGISTRY}/self-service-agent-integration-dispatcher:\$${VERSION})"
	@echo "    MCP_SNOW_IMG                      - Full snow MCP image name (default: \$${REGISTRY}/self-service-agent-snow-mcp:\$${VERSION})"
	@echo "    REQUEST_MGR_IMG                   - Full request manager image name (default: \$${REGISTRY}/self-service-agent-request-manager:\$${VERSION})"
	@echo ""
	@echo "  Model Configuration:"
	@echo "    HF_TOKEN                          - Hugging Face Token (will prompt if not provided)"
	@echo "    LLM_ID                            - Model ID for LLM configuration"
	@echo "    {SAFETY,LLM}                      - Model id as defined in values (eg. llama-3-2-1b-instruct)"
	@echo "    {SAFETY,LLM}_URL                  - Model URL"
	@echo "    {SAFETY,LLM}_API_TOKEN            - Model API token for remote models"
	@echo "    {SAFETY,LLM}_TOLERATION           - Model pod toleration"
	@echo ""
	@echo "  Integration Configuration:"
	@echo "    ENABLE_SLACK                      - Set to 'true' to enable Slack integration and prompt for tokens"
	@echo "    HR_API_KEY                        - HR system integration API key"
	@echo "    SLACK_BOT_TOKEN                   - Slack Bot Token (xoxb-...) for Slack integration"
	@echo "    SLACK_SIGNING_SECRET              - Slack Signing Secret for request verification"
	@echo ""
	@echo "  ServiceNow Configuration:"
	@echo "    SERVICENOW_INSTANCE_URL           - ServiceNow instance URL (e.g., https://dev12345.service-now.com)"
	@echo "    SERVICENOW_USERNAME               - ServiceNow username for authentication"
	@echo "    SERVICENOW_PASSWORD               - ServiceNow password"
	@echo "    SERVICENOW_AUTH_TYPE              - ServiceNow auth type (default: basic)"
	@echo "    USE_REAL_SERVICENOW               - Use real ServiceNow API vs mock data (default: false)"
	@echo ""
	@echo "  Note: Passwords with special characters like \$$ must be set via environment variable:"
	@echo "    export SERVICENOW_PASSWORD='P@ssw0rd\$$r123'  # Required for special chars"
	@echo "    make helm-install-dev NAMESPACE=my-ns USE_REAL_SERVICENOW=true"
	@echo ""
	@echo "  Simple passwords can use either method:"
	@echo "    make helm-install-dev SERVICENOW_PASSWORD=simple123  # No special chars"
	@echo ""
	@echo "  Request Management Layer:"
	@echo "    KNATIVE_EVENTING                  - Enable Knative Eventing (default: true)"
	@echo "    REQUEST_MANAGEMENT                - Enable Request Management Layer (default: true)"

# Build function: $(call build_image,IMAGE_NAME,DESCRIPTION,CONTAINERFILE_PATH,BUILD_CONTEXT)
define build_image
	@echo "Building $(2): $(1)"
	$(CONTAINER_TOOL) build -t $(1) --platform=$(ARCH) $(if $(3),-f $(3),) $(4)
	@echo "Successfully built $(1)"
endef

# Template build function: $(call build_template_image,IMAGE_NAME,DESCRIPTION,SERVICE_NAME,MODULE_NAME,BUILD_CONTEXT)
define build_template_image
	@echo "Building $(2) using template: $(1)"
	$(CONTAINER_TOOL) build -t $(1) --platform=$(ARCH) \
		-f Containerfile.services-template \
		--build-arg SERVICE_NAME=$(3) \
		--build-arg MODULE_NAME=$(4) \
		$(5)
	@echo "Successfully built $(1)"
endef

# MCP template build function: $(call build_mcp_image,IMAGE_NAME,DESCRIPTION,SERVICE_NAME,MODULE_NAME)
define build_mcp_image
	@echo "Building $(2) using MCP template: $(1)"
	$(CONTAINER_TOOL) build -t $(1) --platform=$(ARCH) \
		-f Containerfile.mcp-template \
		--build-arg SERVICE_NAME=$(3) \
		--build-arg MODULE_NAME=$(4) \
		.
	@echo "Successfully built $(1)"
endef

# Push function: $(call push_image,IMAGE_NAME,DESCRIPTION)
define push_image
	@echo "Pushing $(2): $(1)"
	$(CONTAINER_TOOL) push $(1)
	@echo "Successfully pushed $(1)"
endef

# Generic function to get external host for a service
define GET_EXTERNAL_HOST
	if kubectl get route $(1) -n $(NAMESPACE) >/dev/null 2>&1; then \
		kubectl get route $(1) -n $(NAMESPACE) -o jsonpath='{.spec.host}'; \
	elif kubectl get ingress $(1) -n $(NAMESPACE) >/dev/null 2>&1; then \
		kubectl get ingress $(1) -n $(NAMESPACE) -o jsonpath='{.spec.rules[0].host}'; \
	fi
endef

# Generic function to print service URLs
define PRINT_SERVICE_URLS
	@echo "--- Your $(1) URLs are: ---"
	@sleep 10
	@EXTERNAL_HOST=$$($(call GET_EXTERNAL_HOST,$(2))); \
	if [ -n "$$EXTERNAL_HOST" ]; then \
		echo "  OpenAPI Schema: https://$$EXTERNAL_HOST/docs"; \
		echo "  Health: https://$$EXTERNAL_HOST/health"; \
		$(3) \
	else \
		echo "  External access not configured - using cluster-internal URLs:"; \
		echo "  OpenAPI Schema: http://$(MAIN_CHART_NAME)-$(4).$(NAMESPACE).svc.cluster.local/docs"; \
		echo "  Health: http://$(MAIN_CHART_NAME)-$(4).$(NAMESPACE).svc.cluster.local/health"; \
		$(5) \
	fi
endef

define PRINT_REQUEST_MANAGER_URL
	$(call PRINT_SERVICE_URLS,Request Manager,$(INGRESS_PREFIX)-request-manager,,request-manager,)
endef

define PRINT_INTEGRATION_DISPATCHER_URL
	$(call PRINT_SERVICE_URLS,Integration Dispatcher,$(INGRESS_PREFIX)-integration-dispatcher, \
		echo "  Slack Events: https://$$EXTERNAL_HOST/slack/events"; \
		echo "  Slack Interactive: https://$$EXTERNAL_HOST/slack/interactive"; \
		echo "  Slack Commands: https://$$EXTERNAL_HOST/slack/commands";, \
		integration-dispatcher, \
		echo "  Slack Events: http://$(MAIN_CHART_NAME)-integration-dispatcher.$(NAMESPACE).svc.cluster.local/slack/events"; \
		echo "  Slack Interactive: http://$(MAIN_CHART_NAME)-integration-dispatcher.$(NAMESPACE).svc.cluster.local/slack/interactive"; \
		echo "  Slack Commands: http://$(MAIN_CHART_NAME)-integration-dispatcher.$(NAMESPACE).svc.cluster.local/slack/commands";)
endef

# Build container images
.PHONY: build-all-images
build-all-images: build-agent-image build-request-mgr-image build-agent-service-image build-integration-dispatcher-image build-mcp-snow-image build-mock-eventing-image
	@echo "All container images built successfully!"

.PHONY: build-agent-image
build-agent-image: check-lockfile-root
	$(call build_image,$(AGENT_IMG),unified self-service agent image,Containerfile.template,.)


.PHONY: build-request-mgr-image
build-request-mgr-image: check-lockfile-request-manager check-lockfile-shared-models check-lockfile-shared-clients
	$(call build_template_image,$(REQUEST_MGR_IMG),request manager image,request-manager,request_manager.main,.)

.PHONY: build-agent-service-image
build-agent-service-image: check-lockfile-agent-service check-lockfile-shared-models
	$(call build_template_image,$(AGENT_SERVICE_IMG),agent service image,agent-service,agent_service.main,.)

.PHONY: build-integration-dispatcher-image
build-integration-dispatcher-image: check-lockfile-integration-dispatcher check-lockfile-shared-models check-lockfile-shared-clients
	$(call build_template_image,$(INTEGRATION_DISPATCHER_IMG),integration dispatcher image,integration-dispatcher,integration_dispatcher.main,.)

.PHONY: build-mcp-snow-image
build-mcp-snow-image: check-lockfile-mcp-snow
	$(call build_mcp_image,$(MCP_SNOW_IMG),snow MCP image,mcp-servers/snow,snow.server)

.PHONY: build-mock-eventing-image
build-mock-eventing-image: check-lockfile-mock-eventing check-lockfile-shared-models
	@echo "Building mock eventing service image using services template: $(MOCK_EVENTING_IMG)"
	$(CONTAINER_TOOL) build -t $(MOCK_EVENTING_IMG) --platform=$(ARCH) \
		-f Containerfile.services-template \
		--build-arg SERVICE_NAME=mock-eventing-service \
		--build-arg MODULE_NAME=mock_eventing_service.main \
		.
	@echo "Successfully built mock eventing service image: $(MOCK_EVENTING_IMG)"

# Push container images
.PHONY: push-all-images
push-all-images: push-agent-image push-request-mgr-image push-agent-service-image push-integration-dispatcher-image push-mcp-snow-image push-mock-eventing-image
	@echo "All container images pushed successfully!"

.PHONY: push-agent-image
push-agent-image:
	$(call push_image,$(AGENT_IMG) $(PUSH_EXTRA_AGRS),self-service agent image)


.PHONY: push-request-mgr-image
push-request-mgr-image:
	$(call push_image,$(REQUEST_MGR_IMG) $(PUSH_EXTRA_AGRS),request manager image)

.PHONY: push-agent-service-image
push-agent-service-image:
	$(call push_image,$(AGENT_SERVICE_IMG) $(PUSH_EXTRA_AGRS),agent service image)

.PHONY: push-integration-dispatcher-image
push-integration-dispatcher-image:
	$(call push_image,$(INTEGRATION_DISPATCHER_IMG) $(PUSH_EXTRA_AGRS),integration dispatcher image)


.PHONY: push-mcp-snow-image
push-mcp-snow-image:
	$(call push_image,$(MCP_SNOW_IMG) $(PUSH_EXTRA_AGRS),snow MCP image)

.PHONY: push-mock-eventing-image
push-mock-eventing-image:
	$(call push_image,$(MOCK_EVENTING_IMG) $(PUSH_EXTRA_AGRS),mock eventing service image)

# Code quality
.PHONY: lint
lint: format
	@echo "Running comprehensive linting on entire codebase..."
	@echo "1. Running flake8 for code style and basic issues..."
	uv run flake8 .
	@echo "2. Running mypy for import validation (imports only)..."
	uv run mypy --strict \
		$$(find . -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -path "*/.git/*" -not -path "*/node_modules/*")
#		--no-strict-optional --disable-error-code=assignment \
#		--disable-error-code=var-annotated --disable-error-code=attr-defined \
#		--disable-error-code=return-value --disable-error-code=call-overload \
#		--disable-error-code=dict-item --disable-error-code=list-item \
#		--disable-error-code=arg-type --disable-error-code=valid-type \
#		--disable-error-code=misc --disable-error-code=operator \
#		--disable-error-code=union-attr --disable-error-code=type-var \
#		--disable-error-code=call-arg --disable-error-code=annotation-unchecked \
	@echo "3. Running isort to check import organization..."
	uv run isort --check-only --diff .
	@echo "Linting completed successfully!"

lint-strict:
	@echo "Running strict linting on entire codebase..."
	@echo "1. Running flake8 for code style and basic issues..."
	uv run flake8 .
	@echo "2. Running mypy for comprehensive type checking..."
	uv run mypy .
	@echo "3. Running isort to check import organization..."
	uv run isort --check-only --diff .
	@echo "Strict linting completed!"

.PHONY: format
format:
	@echo "Running isort import sorting on entire codebase..."
	uv run isort .
	@echo "Running Black formatting on entire codebase..."
	uv run black .
	@echo "Formatting completed successfully!"

# Install dependencies
.PHONY: install-all
install-all: install-shared-models install-shared-clients install install-asset-manager install-request-manager install-agent-service install-integration-dispatcher install-mcp-snow install-mock-eventing
	@echo "All dependencies installed successfully!"

.PHONY: install-shared-models
install-shared-models:
	@echo "Installing shared models dependencies..."
	cd shared-models && uv sync
	@echo "Shared models dependencies installed successfully!"

.PHONY: install-shared-clients
install-shared-clients:
	@echo "Installing shared clients dependencies..."
	cd shared-clients && uv sync
	@echo "Shared clients dependencies installed successfully!"

.PHONY: install
install:
	@echo "Installing self-service agent dependencies..."
	uv sync
	@echo "Self-service agent dependencies installed successfully!"

.PHONY: reinstall
reinstall:
	@echo "Force reinstalling all dependencies with latest code..."
	@echo "Reinstalling all dependencies with uv sync..."
	uv sync --reinstall
	@echo "All dependencies reinstalled with latest code!"

.PHONY: reinstall-all
reinstall-all: reinstall-shared-models reinstall-shared-clients reinstall reinstall-asset-manager reinstall-request-manager reinstall-agent-service reinstall-integration-dispatcher reinstall-mcp-snow
	@echo "All project dependencies reinstalled successfully!"

.PHONY: reinstall-asset-manager
reinstall-asset-manager:
	@echo "Force reinstalling asset manager dependencies..."
	cd asset-manager && uv sync --reinstall
	@echo "Asset manager dependencies reinstalled successfully!"

.PHONY: reinstall-request-manager
reinstall-request-manager:
	@echo "Force reinstalling request manager dependencies..."
	cd request-manager && uv sync --reinstall
	@echo "Request manager dependencies reinstalled successfully!"

.PHONY: reinstall-agent-service
reinstall-agent-service:
	@echo "Force reinstalling agent service dependencies..."
	cd agent-service && uv sync --reinstall
	@echo "Agent service dependencies reinstalled successfully!"

.PHONY: reinstall-integration-dispatcher
reinstall-integration-dispatcher:
	@echo "Force reinstalling integration dispatcher dependencies..."
	cd integration-dispatcher && uv sync --reinstall
	@echo "Integration dispatcher dependencies reinstalled successfully!"

.PHONY: reinstall-mcp-snow
reinstall-mcp-snow:
	@echo "Force reinstalling snow MCP dependencies..."
	cd mcp-servers/snow && uv sync --reinstall
	@echo "Snow MCP dependencies reinstalled successfully!"

.PHONY: reinstall-shared-models
reinstall-shared-models:
	@echo "Force reinstalling shared models dependencies..."
	cd shared-models && uv sync --reinstall
	@echo "Shared models dependencies reinstalled successfully!"

.PHONY: reinstall-shared-clients
reinstall-shared-clients:
	@echo "Force reinstalling shared clients dependencies..."
	cd shared-clients && uv sync --reinstall
	@echo "Shared clients dependencies reinstalled successfully!"

.PHONY: install-asset-manager
install-asset-manager:
	@echo "Installing asset manager dependencies..."
	cd asset-manager && uv sync
	@echo "Asset manager dependencies installed successfully!"

.PHONY: install-request-manager
install-request-manager:
	@echo "Installing request manager dependencies..."
	cd request-manager && uv sync
	@echo "Request manager dependencies installed successfully!"

.PHONY: install-agent-service
install-agent-service:
	@echo "Installing agent service dependencies..."
	cd agent-service && uv sync
	@echo "Agent service dependencies installed successfully!"

.PHONY: install-integration-dispatcher
install-integration-dispatcher:
	@echo "Installing integration dispatcher dependencies..."
	cd integration-dispatcher && uv sync
	@echo "Integration dispatcher dependencies installed successfully!"

.PHONY: install-mcp-snow
install-mcp-snow:
	@echo "Installing snow MCP dependencies..."
	cd mcp-servers/snow && uv sync
	@echo "Snow MCP dependencies installed successfully!"

.PHONY: install-mock-eventing
install-mock-eventing:
	@echo "Installing mock eventing service dependencies..."
	cd mock-eventing-service && uv sync
	@echo "Mock eventing service dependencies installed successfully!"

# Test code
.PHONY: test-all
test-all: test-shared-models test-shared-clients test test-asset-manager test-request-manager test-agent-service test-integration-dispatcher test-mcp-snow
	@echo "All tests completed successfully!"

# Lockfile management
define check_lockfile
	@echo "📦 Checking $(1)..."
	@if [ -d "$(1)" ]; then \
		if (cd "$(1)" && uv lock --check); then \
			echo "✅ $(1) lockfile is up-to-date"; \
		else \
			echo "❌ $(1) lockfile needs updating"; \
			exit 1; \
		fi; \
	else \
		echo "⚠️  $(1) directory not found, skipping..."; \
	fi
endef

define update_lockfile
	@echo "📦 Updating $(1)..."
	@if [ -d "$(1)" ]; then \
		cd "$(1)" && uv lock; \
		echo "✅ $(1) lockfile updated"; \
	else \
		echo "⚠️  $(1) directory not found, skipping..."; \
	fi
endef

.PHONY: check-lockfiles
check-lockfiles:
	@echo "🔍 Checking uv.lock files across all services..."
	@echo
	@echo "📦 Checking root project..."
	@if uv lock --check; then \
		echo "✅ Root project lockfile is up-to-date"; \
	else \
		echo "❌ Root project lockfile needs updating"; \
		exit 1; \
	fi
	@echo
	$(call check_lockfile,shared-models)
	@echo
	$(call check_lockfile,shared-clients)
	@echo
	$(call check_lockfile,agent-service)
	@echo
	$(call check_lockfile,request-manager)
	@echo
	$(call check_lockfile,integration-dispatcher)
	@echo
	$(call check_lockfile,mcp-servers/snow)
	@echo
	$(call check_lockfile,mock-eventing-service)
	@echo
	$(call check_lockfile,asset-manager)
	@echo
	@echo "🎉 All lockfiles are up-to-date!"

.PHONY: update-lockfiles
update-lockfiles:
	@echo "🔄 Updating all uv.lock files..."
	@echo
	@echo "📦 Updating root project..."
	@uv lock
	@echo "✅ Root project lockfile updated"
	@echo
	$(call update_lockfile,shared-models)
	@echo
	$(call update_lockfile,shared-clients)
	@echo
	$(call update_lockfile,agent-service)
	@echo
	$(call update_lockfile,request-manager)
	@echo
	$(call update_lockfile,integration-dispatcher)
	@echo
	$(call update_lockfile,mcp-servers/snow)
	@echo
	$(call update_lockfile,mock-eventing-service)
	@echo
	$(call update_lockfile,asset-manager)
	@echo
	@echo "🎉 All lockfiles updated successfully!"

# Individual service lockfile targets
.PHONY: check-lockfile-root check-lockfile-shared-models check-lockfile-shared-clients check-lockfile-agent-service check-lockfile-request-manager check-lockfile-integration-dispatcher check-lockfile-mcp-snow check-lockfile-mock-eventing check-lockfile-asset-manager
check-lockfile-root:
	@echo "📦 Checking root project..."
	@if uv lock --check; then \
		echo "✅ Root project lockfile is up-to-date"; \
	else \
		echo "❌ Root project lockfile needs updating"; \
		exit 1; \
	fi
check-lockfile-shared-models:
	$(call check_lockfile,shared-models)

check-lockfile-shared-clients:
	$(call check_lockfile,shared-clients)

check-lockfile-agent-service:
	$(call check_lockfile,agent-service)

check-lockfile-request-manager:
	$(call check_lockfile,request-manager)

check-lockfile-integration-dispatcher:
	$(call check_lockfile,integration-dispatcher)

check-lockfile-mcp-snow:
	$(call check_lockfile,mcp-servers/snow)

check-lockfile-mock-eventing:
	$(call check_lockfile,mock-eventing-service)

check-lockfile-asset-manager:
	$(call check_lockfile,asset-manager)

.PHONY: update-lockfile-shared-models update-lockfile-shared-clients update-lockfile-agent-service update-lockfile-request-manager update-lockfile-integration-dispatcher update-lockfile-mcp-snow update-lockfile-mock-eventing update-lockfile-asset-manager
update-lockfile-shared-models:
	$(call update_lockfile,shared-models)

update-lockfile-shared-clients:
	$(call update_lockfile,shared-clients)

update-lockfile-agent-service:
	$(call update_lockfile,agent-service)

update-lockfile-request-manager:
	$(call update_lockfile,request-manager)

update-lockfile-integration-dispatcher:
	$(call update_lockfile,integration-dispatcher)

update-lockfile-mcp-snow:
	$(call update_lockfile,mcp-servers/snow)

update-lockfile-mock-eventing:
	$(call update_lockfile,mock-eventing-service)

update-lockfile-asset-manager:
	$(call update_lockfile,asset-manager)

.PHONY: test-shared-models
test-shared-models:
	@echo "Running shared models tests..."
	cd shared-models && uv run python -m pytest || echo "No tests found for shared-models"
	@echo "Shared models tests completed successfully!"

.PHONY: test-shared-clients
test-shared-clients:
	@echo "Running shared clients tests..."
	cd shared-clients && uv run python -m pytest tests/ || echo "No tests found for shared-clients"
	@echo "Shared clients tests completed successfully!"

.PHONY: test
test:
	@echo "Running self-service agent tests..."
	uv run python -m pytest test/ || echo "No tests found in self-service agent test directory"
	@echo "Self-service agent test check completed!"

.PHONY: test-asset-manager
test-asset-manager:
	@echo "Running asset manager tests..."
	cd asset-manager && uv run python -m pytest tests/
	@echo "Asset manager tests completed successfully!"

.PHONY: test-request-manager
test-request-manager:
	@echo "Running request manager tests..."
	cd request-manager && uv run python -m pytest tests/
	@echo "Request manager tests completed successfully!"

.PHONY: test-agent-service
test-agent-service:
	@echo "Running agent service tests..."
	cd agent-service && uv run python -m pytest tests/ || echo "No tests found in agent service test directory"
	@echo "Agent service test check completed!"

.PHONY: test-integration-dispatcher
test-integration-dispatcher:
	@echo "Running integration dispatcher tests..."
	cd integration-dispatcher && uv run python -m pytest tests/ || echo "No tests found for integration dispatcher"
	@echo "Integration dispatcher tests completed successfully!"

.PHONY: test-mcp-snow
test-mcp-snow:
	@echo "Running snow MCP tests..."
	cd mcp-servers/snow && uv run python -m pytest tests/
	@echo "Snow MCP tests completed successfully!"

.PHONY: sync-evaluations
sync-evaluations:
	@echo "Syncing evaluations libraries"
	uv --directory evaluations sync
	@echo "Syncing evaluations libraries completed successfully!"

.PHONY: test-short-integration-request-mgr
test-short-integration-request-mgr:
	@echo "Running short integration test with Request Manager..."
	uv --directory evaluations run evaluate.py -n 1 --test-script chat-request-mgr.py
	@echo "short integrations tests with Request Manager completed successfully!"

.PHONY: test-short-resp-integration-request-mgr
test-short-resp-integration-request-mgr:
	@echo "Running short responses integration test with Request Manager..."
	uv --directory evaluations run evaluate.py -n 1 --no-employee-id --test-script chat-responses-request-mgr.py --reset-conversation
	@echo "short responses integrations tests with Request Manager completed successfully!"

.PHONY: test-long-resp-integration-request-mgr
test-long-resp-integration-request-mgr:
	@echo "Running long responses integration test with Request Manager..."
	uv --directory evaluations run evaluate.py -n 20 --no-employee-id --test-script chat-responses-request-mgr.py --reset-conversation --timeout=1800
	@echo "long responses integrations tests with Request Manager completed successfully!"

# Create namespace and deploy
namespace:
	@kubectl create namespace $(NAMESPACE) &> /dev/null && kubectl label namespace $(NAMESPACE) modelmesh-enabled=false ||:
	@kubectl config set-context --current --namespace=$(NAMESPACE) &> /dev/null ||:

.PHONY: helm-depend
helm-depend:
	@echo "Updating Helm dependencies"
	@helm dependency update helm &> /dev/null

.PHONY: helm-list-models
helm-list-models: helm-depend
	@helm template dummy-release helm --set llm-service._debugListModels=true | grep ^model:

# Common function for helm installation with different eventing modes
define helm_install_common
	@$(eval PGVECTOR_ARGS := $(helm_pgvector_args))
	@$(eval LLM_SERVICE_ARGS := $(helm_llm_service_args))
	@$(eval LLAMA_STACK_ARGS := $(helm_llama_stack_args))
	@$(eval REQUEST_MANAGEMENT_ARGS := $(helm_request_management_args))
	@$(eval LOG_LEVEL_ARGS := $(if $(LOG_LEVEL),--set logLevel=$(LOG_LEVEL),))

	@if [ "$(USE_REAL_SERVICENOW)" = "true" ]; then \
		echo "Creating ServiceNow credentials secret..."; \
		if [ -n "$$SERVICENOW_PASSWORD" ] || [ -n "$$SERVICENOW_USERNAME" ] || [ -n "$$SERVICENOW_INSTANCE_URL" ]; then \
			kubectl create secret generic $(MAIN_CHART_NAME)-servicenow-credentials \
				--from-literal=servicenow-instance-url="$${SERVICENOW_INSTANCE_URL:-}" \
				--from-literal=servicenow-username="$${SERVICENOW_USERNAME:-}" \
				--from-literal=servicenow-password="$${SERVICENOW_PASSWORD:-}" \
				-n $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -; \
		else \
			echo "⚠️  WARNING: USE_REAL_SERVICENOW=true but ServiceNow credentials not provided"; \
		fi; \
	else \
		echo "Skipping ServiceNow credentials secret (USE_REAL_SERVICENOW=false)"; \
	fi

	@echo "Cleaning up any existing jobs..."
	@kubectl delete job -l app.kubernetes.io/component=init -n $(NAMESPACE) --ignore-not-found || true
	@kubectl delete job -l app.kubernetes.io/name=self-service-agent -n $(NAMESPACE) --ignore-not-found || true
	@echo "Installing $(MAIN_CHART_NAME) helm chart $(1)"
	@helm upgrade --install $(MAIN_CHART_NAME) helm -n $(NAMESPACE) \
		--set image.repository=self-service-agent \
		--set image.requestManager=self-service-agent-request-manager \
		--set image.agentService=self-service-agent-service \
		--set image.integrationDispatcher=self-service-agent-integration-dispatcher \
		--set image.tag=$(VERSION) \
		$(PGVECTOR_ARGS) \
		$(LLM_SERVICE_ARGS) \
		$(LLAMA_STACK_ARGS) \
		--set requestManagement.integrations.slack.enabled=$(SLACK_ENABLED) \
		$(if $(filter true,$(SLACK_ENABLED)),--set security.slack.signingSecret=$(SLACK_SIGNING_SECRET) --set security.slack.botToken=$(SLACK_BOT_TOKEN),) \
		--set image.registry=$(REGISTRY) \
		--set mcp-servers.mcp-servers.self-service-agent-snow.imageRepository=$(REGISTRY)/self-service-agent-snow-mcp \
		--set mcp-servers.mcp-servers.self-service-agent-snow.imageTag=$(VERSION) \
		--set-string mcp-servers.mcp-servers.self-service-agent-snow.env.SERVICENOW_AUTH_TYPE="$(SERVICENOW_AUTH_TYPE)" \
		--set-string mcp-servers.mcp-servers.self-service-agent-snow.env.USE_REAL_SERVICENOW="$(USE_REAL_SERVICENOW)" \
		$(if $(filter true,$(USE_REAL_SERVICENOW)),--set mcp-servers.mcp-servers.self-service-agent-snow.envFromSecret.SERVICENOW_INSTANCE_URL.name=$(MAIN_CHART_NAME)-servicenow-credentials --set mcp-servers.mcp-servers.self-service-agent-snow.envFromSecret.SERVICENOW_INSTANCE_URL.key=servicenow-instance-url --set mcp-servers.mcp-servers.self-service-agent-snow.envFromSecret.SERVICENOW_USERNAME.name=$(MAIN_CHART_NAME)-servicenow-credentials --set mcp-servers.mcp-servers.self-service-agent-snow.envFromSecret.SERVICENOW_USERNAME.key=servicenow-username --set mcp-servers.mcp-servers.self-service-agent-snow.envFromSecret.SERVICENOW_PASSWORD.name=$(MAIN_CHART_NAME)-servicenow-credentials --set mcp-servers.mcp-servers.self-service-agent-snow.envFromSecret.SERVICENOW_PASSWORD.key=servicenow-password,) \
		$(REQUEST_MANAGEMENT_ARGS) \
		$(LOG_LEVEL_ARGS) \
		$(if $(filter-out "",$(2)),$(2),) \
		$(EXTRA_HELM_ARGS)
	@echo "Waiting for main chart deployment..."
	@kubectl rollout status deploy/$(MAIN_CHART_NAME) -n $(NAMESPACE) --timeout 10m
	@echo "Waiting for request manager deployment..."
	@kubectl rollout status deploy/$(MAIN_CHART_NAME)-request-manager -n $(NAMESPACE) --timeout 10m
	@echo "Waiting for integration dispatcher deployment..."
	@kubectl rollout status deploy/$(MAIN_CHART_NAME)-integration-dispatcher -n $(NAMESPACE) --timeout 10m
	@echo "Waiting for agent service deployment..."
	@kubectl rollout status deploy/$(MAIN_CHART_NAME)-agent-service -n $(NAMESPACE) --timeout 10m
	$(if $(filter true,$(3)),@echo "Waiting for mock eventing deployment..." && kubectl rollout status deploy/$(MAIN_CHART_NAME)-mock-eventing -n $(NAMESPACE) --timeout 5m,)
	@echo "$(MAIN_CHART_NAME) $(1) installed successfully"
endef

# Install with direct HTTP communication (development mode)
.PHONY: helm-install-dev
helm-install-dev: namespace helm-depend
	$(call helm_install_common,"with direct HTTP communication - development",\
		"",\
		false)
	@$(MAKE) print-urls

# Install with mock eventing service (testing/CI mode)
.PHONY: helm-install-test
helm-install-test: namespace helm-depend
	$(call helm_install_common,"with mock eventing service - testing/CI",\
		-f helm/values-test.yaml \
		--set requestManagement.knative.mockEventing.enabled=true \
		--set testIntegrationEnabled=true,\
		true)
	@$(MAKE) print-urls

# Install with full Knative eventing (production mode)
.PHONY: helm-install-prod
helm-install-prod: namespace helm-depend
	@echo "Installing with retry logic for triggers..."
	@for i in 1 2 3; do \
		echo "Attempt $$i of 3..."; \
		if $(MAKE) _helm-install-prod-single; then \
			echo "Installation successful, verifying triggers..."; \
			EXPECTED_TRIGGERS=10; \
			ACTUAL_TRIGGERS=$$(kubectl get triggers -n $(NAMESPACE) --no-headers 2>/dev/null | wc -l); \
			if [ "$$ACTUAL_TRIGGERS" -eq "$$EXPECTED_TRIGGERS" ]; then \
				echo "✅ All $$EXPECTED_TRIGGERS triggers deployed successfully"; \
				@$(MAKE) print-urls; \
				exit 0; \
			else \
				echo "❌ Only $$ACTUAL_TRIGGERS out of $$EXPECTED_TRIGGERS triggers deployed"; \
				if [ $$i -lt 3 ]; then \
					echo "Attempt $$i failed, waiting 30s before retry..."; \
					sleep 30; \
				fi; \
			fi; \
		else \
			echo "Installation failed on attempt $$i"; \
			if [ $$i -lt 3 ]; then \
				echo "Waiting 30s before retry..."; \
				sleep 30; \
			fi; \
		fi; \
	done; \
	echo "❌ Failed to deploy all triggers after 3 attempts"; \
	exit 1

# Single installation attempt for production
.PHONY: _helm-install-prod-single
_helm-install-prod-single:
	@$(call helm_install_common,"with full Knative eventing - production",\
		-f helm/values-production.yaml \
		--set requestManagement.knative.eventing.enabled=true,\
		false)

# Print service URLs (used after successful installation)
.PHONY: print-urls
print-urls:
	@$(PRINT_REQUEST_MANAGER_URL)
	@$(PRINT_INTEGRATION_DISPATCHER_URL)

# Verify all expected Knative triggers are deployed
.PHONY: verify-triggers
verify-triggers:
	@echo "Verifying all triggers are deployed..."
	@EXPECTED_TRIGGERS=10; \
	ACTUAL_TRIGGERS=$$(kubectl get triggers -n $(NAMESPACE) --no-headers 2>/dev/null | wc -l); \
	if [ "$$ACTUAL_TRIGGERS" -eq "$$EXPECTED_TRIGGERS" ]; then \
		echo "✅ All $$EXPECTED_TRIGGERS triggers deployed successfully"; \
		kubectl get triggers -n $(NAMESPACE) --no-headers | awk '{print "  - " $$1}'; \
	else \
		echo "❌ Only $$ACTUAL_TRIGGERS out of $$EXPECTED_TRIGGERS triggers deployed"; \
		echo ""; \
		echo "Deployed triggers:"; \
		kubectl get triggers -n $(NAMESPACE) --no-headers 2>/dev/null | awk '{print "  ✅ " $$1}' || echo "  (none)"; \
		echo ""; \
		echo "Expected triggers:"; \
		echo "  ✅ self-service-agent-request-created-trigger"; \
		echo "  ✅ self-service-agent-agent-response-trigger"; \
		echo "  ✅ self-service-agent-routing-trigger"; \
		echo "  ✅ self-service-agent-agent-response-to-request-manager-trigger"; \
		echo "  ✅ self-service-agent-request-notification-trigger"; \
		echo "  ✅ self-service-agent-processing-notification-trigger"; \
		echo "  ❌ self-service-agent-database-update-trigger"; \
		echo "  ❌ self-service-agent-responses-request-trigger"; \
		echo "  ❌ self-service-agent-responses-response-to-request-manager-trigger"; \
		echo "  ❌ self-service-agent-responses-response-to-integration-dispatcher-trigger"; \
		echo ""; \
		echo "To fix missing triggers, run:"; \
		echo "  make helm-install-prod"; \
		exit 1; \
	fi

# Uninstall the deployment and clean up
.PHONY: helm-uninstall
helm-uninstall:
	@echo "Enhanced uninstall process for $(MAIN_CHART_NAME) helm chart in namespace $(NAMESPACE)"

	@echo "Step 1: Attempting normal helm uninstall..."
	@helm uninstall --ignore-not-found $(MAIN_CHART_NAME) -n $(NAMESPACE) || echo "Normal uninstall failed, proceeding with enhanced cleanup..."

	@echo "Step 2: Manual cleanup of namespace-scoped Knative resources..."
	@echo "Cleaning up Knative Eventing resources in $(NAMESPACE) only..."
	@kubectl delete triggers -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@kubectl delete broker -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@echo "Cleaning up Knative-related ConfigMaps..."
	@kubectl delete configmap -n $(NAMESPACE) -l app.kubernetes.io/name=self-service-agent --ignore-not-found || true
	@echo "Force cleanup any stuck resources with finalizers..."
	@kubectl get broker -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} kubectl patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' || true
	@kubectl get trigger -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} kubectl patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' || true

	@echo "Step 3: Retry helm uninstall after cleanup..."
	@helm uninstall --ignore-not-found $(MAIN_CHART_NAME) -n $(NAMESPACE) || echo "Helm uninstall completed with manual cleanup"

	@echo "Step 4: Final cleanup of namespace $(NAMESPACE)..."
	@$(MAKE) helm-cleanup-jobs
	@echo "Removing ServiceNow credentials secret from $(NAMESPACE)"
	@kubectl delete secret $(MAIN_CHART_NAME)-servicenow-credentials -n $(NAMESPACE) --ignore-not-found || true
	@echo "Removing pgvector and init job PVCs from $(NAMESPACE)"
	@kubectl get pvc -n $(NAMESPACE) -o custom-columns=NAME:.metadata.name | grep -E '^(pg.*-data|self-service-agent-init-status)' | xargs -I {} kubectl delete pvc -n $(NAMESPACE) {} ||:
	@echo "Deleting remaining pods in namespace $(NAMESPACE)"
	@kubectl delete pods -n $(NAMESPACE) --all || true
	@echo "Checking for any remaining resources in namespace $(NAMESPACE)..."
	@echo "If you want to completely remove the namespace, run: kubectl delete namespace $(NAMESPACE)"
	@echo "Remaining resources in namespace $(NAMESPACE):"
	@$(MAKE) helm-status

# Manual cleanup for Knative Eventing resources (useful for webhook timeout issues)
.PHONY: helm-cleanup-eventing
helm-cleanup-eventing:
	@echo "Manual cleanup of Knative Eventing resources in $(NAMESPACE)..."
	@echo "Step 1: Attempting normal deletion with short timeout..."
	@kubectl delete triggers -n $(NAMESPACE) --all --ignore-not-found --timeout=10s || echo "Normal trigger deletion failed, proceeding with force cleanup..."
	@kubectl delete broker -n $(NAMESPACE) --all --ignore-not-found --timeout=10s || echo "Normal broker deletion failed, proceeding with force cleanup..."
	@kubectl delete configmap -n $(NAMESPACE) -l app.kubernetes.io/name=self-service-agent --ignore-not-found || true
	@echo "Step 2: Force cleanup any stuck resources with finalizers..."
	@echo "Attempting to patch finalizers on brokers..."
	@kubectl get broker -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} kubectl patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' --timeout=10s || echo "Patch failed, trying force delete..."
	@echo "Attempting to patch finalizers on triggers..."
	@kubectl get trigger -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} kubectl patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' --timeout=10s || echo "Patch failed, trying force delete..."
	@echo "Step 3: Force delete with zero grace period..."
	@kubectl get broker -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} kubectl delete {} -n $(NAMESPACE) --force --grace-period=0 || echo "Force delete failed, resource may need manual intervention"
	@kubectl get trigger -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} kubectl delete {} -n $(NAMESPACE) --force --grace-period=0 || echo "Force delete failed, resource may need manual intervention"
	@echo "Step 4: Final verification..."
	@echo "Remaining Knative Eventing resources in $(NAMESPACE):"
	@kubectl get broker,trigger -n $(NAMESPACE) 2>/dev/null || echo "No Knative Eventing resources found"
	@echo "Knative Eventing cleanup completed for namespace $(NAMESPACE). If resources still exist, they may require cluster admin intervention to resolve webhook issues."

# Clean up leftover jobs
.PHONY: helm-cleanup-jobs
helm-cleanup-jobs:
	@echo "Cleaning up leftover jobs in namespace $(NAMESPACE)..."
	@kubectl delete jobs -n $(NAMESPACE) -l app.kubernetes.io/name=self-service-agent --ignore-not-found || true
	@echo "Job cleanup completed for namespace $(NAMESPACE)"

# Check deployment status
.PHONY: helm-status
helm-status:
	@echo "Listing pods..."
	kubectl get pods -n $(NAMESPACE) || true

	@echo "Listing services..."
	kubectl get svc -n $(NAMESPACE) || true

	@echo "Listing routes..."
	kubectl get routes -n $(NAMESPACE) || true

	@echo "Listing secrets..."
	kubectl get secrets -n $(NAMESPACE) | grep huggingface-secret || true

	@echo "Listing pvcs..."
	kubectl get pvc -n $(NAMESPACE) || true

.PHONY: oc
export OC = ./bin/oc
oc: ## Download oc locally if necessary.
ifeq (,$(wildcard $(OC)))
ifeq (,$(shell which oc 2>/dev/null))
	@{ \
	set -e ;\
	mkdir -p $(dir $(OC)) ;\
	curl -sSLo oc.tar.gz https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/4.11.6/openshift-client-linux.tar.gz ;\
	tar -xf oc.tar.gz -C $(dir $(OC)) oc ;\
	}
else
OC = $(shell which oc)
endif
endif
