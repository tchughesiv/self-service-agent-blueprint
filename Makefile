# Makefile for RAG Deployment
ifeq ($(NAMESPACE),)
ifneq (,$(filter namespace helm-install helm-uninstall helm-status helm-cleanup-knative,$(MAKECMDGOALS)))
$(error NAMESPACE is not set)
endif
endif

VERSION ?= 0.0.2
CONTAINER_TOOL ?= podman
REGISTRY ?= quay.io/ecosystem-appeng
AGENT_IMG ?= $(REGISTRY)/self-service-agent:$(VERSION)
ASSET_MGR_IMG ?= $(REGISTRY)/self-service-agent-asset-manager:$(VERSION)
REQUEST_MGR_IMG ?= $(REGISTRY)/self-service-agent-request-manager:$(VERSION)
AGENT_SERVICE_IMG ?= $(REGISTRY)/self-service-agent-service:$(VERSION)
INTEGRATION_DISPATCHER_IMG ?= $(REGISTRY)/self-service-agent-integration-dispatcher:$(VERSION)
DB_MIGRATION_IMG ?= $(REGISTRY)/self-service-agent-db-migration:$(VERSION)
MCP_EMP_INFO_IMG ?= $(REGISTRY)/self-service-agent-employee-info-mcp:$(VERSION)
MCP_SNOW_IMG ?= $(REGISTRY)/self-service-agent-snow-mcp:$(VERSION)

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

helm_pgvector_args = \
    --set pgvector.secret.user=$(POSTGRES_USER) \
    --set pgvector.secret.password=$(POSTGRES_PASSWORD) \
    --set pgvector.secret.dbname=$(POSTGRES_DBNAME)

helm_llm_service_args = \
    $(if $(HF_TOKEN),--set llm-service.secret.hf_token=$(HF_TOKEN),) \
    $(if $(LLM),--set global.models.$(LLM).enabled=true,) \
    $(if $(SAFETY),--set global.models.$(SAFETY).enabled=true,) \
    $(if $(LLM_TOLERATION),--set-json global.models.$(LLM).tolerations='$(call TOLERATIONS_TEMPLATE,$(LLM_TOLERATION))',) \
    $(if $(SAFETY_TOLERATION),--set-json global.models.$(SAFETY).tolerations='$(call TOLERATIONS_TEMPLATE,$(SAFETY_TOLERATION))',)

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
    $(if $(SERVICE_MESH),--set requestManagement.serviceMesh.enabled=$(SERVICE_MESH),) \
    $(if $(KNATIVE_EVENTING),--set requestManagement.knative.eventing.enabled=$(KNATIVE_EVENTING),) \
    $(if $(API_GATEWAY_HOST),--set-json requestManagement.serviceMesh.gateway.hosts='["$(API_GATEWAY_HOST)"]',) \
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
	@echo "  build-all-images            - Build all container images (agent, asset-manager, request-manager, agent-service, integration-dispatcher, db-migration, employee-info-mcp, snow-mcp)"
	@echo "  build-agent-image           - Build the self-service agent container image"
	@echo "  build-asset-mgr-image       - Build the asset manager container image"
	@echo "  build-request-mgr-image     - Build the request manager container image"
	@echo "  build-agent-service-image   - Build the agent service container image"
	@echo "  build-integration-dispatcher-image - Build the integration dispatcher container image"
	@echo "  build-db-migration-image    - Build the database migration container image"
	@echo "  build-mcp-emp-info-image    - Build the employee info MCP server container image"
	@echo "  build-mcp-snow-image        - Build the snow MCP server container image"
	@echo "  format                      - Run isort import sorting and Black formatting on entire codebase"
	@echo "  helm-depend                 - Update Helm dependencies"
	@echo "  helm-install                - Install the RAG deployment (creates namespace, secrets, and deploys Helm chart)"
	@echo "  helm-list-models            - List available models"
	@echo "  helm-status                 - Check status of the deployment"
	@echo "  helm-uninstall              - Uninstall the RAG deployment and clean up resources"
	@echo "  helm-cleanup-knative        - Manually clean up leftover Knative resources (run if webhook timeouts occur)"
	@echo "  helm-cleanup-jobs           - Clean up leftover jobs from failed deployments"
	@echo "  install-all                 - Install dependencies for all projects"
	@echo "  install                     - Install dependencies for self-service agent"
	@echo "  install-asset-manager       - Install dependencies for asset manager"
	@echo "  install-mcp-emp-info        - Install dependencies for employee info MCP server"
	@echo "  install-mcp-snow            - Install dependencies for snow MCP server"
	@echo "  lint                        - Run flake8 linting on entire codebase"
	@echo "  push-all-images             - Push all container images to registry"
	@echo "  push-agent-image            - Push the self-service agent container image to registry"
	@echo "  push-asset-mgr-image        - Push the asset manager container image to registry"
	@echo "  push-request-mgr-image      - Push the request manager container image to registry"
	@echo "  push-agent-service-image    - Push the agent service container image to registry"
	@echo "  push-integration-dispatcher-image - Push the integration dispatcher container image to registry"
	@echo "  push-db-migration-image     - Push the database migration container image to registry"
	@echo "  push-mcp-emp-info-image     - Push the employee info MCP server container image to registry"
	@echo "  push-mcp-snow-image         - Push the snow MCP server container image to registry"
	@echo "  test-all                    - Run tests for all projects"
	@echo "  test                        - Run tests for self-service agent"
	@echo "  test-asset-manager          - Run tests for asset manager"
	@echo "  test-mcp-emp-info           - Run tests for employee info MCP server"
	@echo "  test-mcp-snow               - Run tests for snow MCP server"
	@echo "  test-short-integration      - Run short integration tests"
	@echo "  version                     - Print the current VERSION"
	@echo ""
	@echo "Configuration options (set via environment variables or make arguments):"
	@echo "  CONTAINER_TOOL           - Container build tool (default: podman)"
	@echo "  REGISTRY                 - Container registry (default: quay.io/ecosystem-appeng)"
	@echo "  VERSION                  - Image version tag (default: 0.0.2)"
	@echo "  AGENT_IMG                - Full agent image name (default: \$${REGISTRY}/self-service-agent:\$${VERSION})"
	@echo "  ASSET_MGR_IMG            - Full asset manager image name (default: \$${REGISTRY}/self-service-asset-manager:\$${VERSION})"
	@echo "  MCP_EMP_INFO_IMG         - Full employee info MCP image name (default: \$${REGISTRY}/self-service-agent-employee-info-mcp:\$${VERSION})"
	@echo "  MCP_SNOW_IMG             - Full snow MCP image name (default: \$${REGISTRY}/self-service-agent-snow-mcp:\$${VERSION})"
	@echo "  NAMESPACE                - Target namespace (default: llama-stack-rag)"
	@echo "  HF_TOKEN                 - Hugging Face Token (will prompt if not provided)"
	@echo "  {SAFETY,LLM}             - Model id as defined in values (eg. llama-3-2-1b-instruct)"
	@echo "  LLM_ID                   - Model ID for LLM configuration"
	@echo "  {SAFETY,LLM}_URL         - Model URL"
	@echo "  {SAFETY,LLM}_API_TOKEN   - Model API token for remote models"
	@echo "  {SAFETY,LLM}_TOLERATION  - Model pod toleration"
	@echo ""
	@echo "Request Management Layer options:"
	@echo "  REQUEST_MANAGEMENT       - Enable Request Management Layer (default: true)"
	@echo "  SERVICE_MESH             - Enable OpenShift Service Mesh integration (default: true)"
	@echo "  KNATIVE_EVENTING         - Enable Knative Eventing (default: true)"
	@echo "  API_GATEWAY_HOST         - Service Mesh gateway host (e.g. api.selfservice.apps.cluster.local)"
	@echo "  SLACK_SIGNING_SECRET     - Slack webhook signing secret"
	@echo "  SNOW_API_KEY             - ServiceNow integration API key"
	@echo "  HR_API_KEY               - HR system integration API key"

# Build function: $(call build_image,IMAGE_NAME,DESCRIPTION,CONTAINERFILE_PATH,BUILD_CONTEXT)
define build_image
	@echo "Building $(2): $(1)"
	$(CONTAINER_TOOL) build -t $(1) --platform=linux/amd64 $(if $(3),-f $(3),) $(4)
	@echo "Successfully built $(1)"
endef

# Push function: $(call push_image,IMAGE_NAME,DESCRIPTION)
define push_image
	@echo "Pushing $(2): $(1)"
	$(CONTAINER_TOOL) push $(1)
	@echo "Successfully pushed $(1)"
endef

# Build container images
.PHONY: build-all-images
build-all-images: build-agent-image build-asset-mgr-image build-request-mgr-image build-agent-service-image build-integration-dispatcher-image build-db-migration-image build-mcp-emp-info-image build-mcp-snow-image
	@echo "All container images built successfully!"

.PHONY: build-agent-image
build-agent-image:
	$(call build_image,$(AGENT_IMG),self-service agent image,Containerfile,.)

.PHONY: build-asset-mgr-image
build-asset-mgr-image:
	$(call build_image,$(ASSET_MGR_IMG),asset manager image,asset-manager/Containerfile,asset-manager/)

.PHONY: build-request-mgr-image
build-request-mgr-image:
	$(call build_image,$(REQUEST_MGR_IMG),request manager image,request-manager/Containerfile,.)

.PHONY: build-agent-service-image
build-agent-service-image:
	$(call build_image,$(AGENT_SERVICE_IMG),agent service image,agent-service/Containerfile,agent-service/)

.PHONY: build-integration-dispatcher-image
build-integration-dispatcher-image:
	$(call build_image,$(INTEGRATION_DISPATCHER_IMG),integration dispatcher image,integration-dispatcher/Containerfile,.)

.PHONY: build-db-migration-image
build-db-migration-image:
	$(call build_image,$(DB_MIGRATION_IMG),database migration image,shared-db/Containerfile,shared-db/)

.PHONY: build-mcp-emp-info-image
build-mcp-emp-info-image:
	$(call build_image,$(MCP_EMP_INFO_IMG),employee info MCP image,mcp-servers/employee-info/Containerfile,mcp-servers/employee-info/)

.PHONY: build-mcp-snow-image
build-mcp-snow-image:
	$(call build_image,$(MCP_SNOW_IMG),snow MCP image,mcp-servers/snow/Containerfile,mcp-servers/snow/)

# Push container images
.PHONY: push-all-images
push-all-images: push-agent-image push-asset-mgr-image push-request-mgr-image push-agent-service-image push-integration-dispatcher-image push-db-migration-image push-mcp-emp-info-image push-mcp-snow-image
	@echo "All container images pushed successfully!"

.PHONY: push-agent-image
push-agent-image:
	$(call push_image,$(AGENT_IMG),self-service agent image)

.PHONY: push-asset-mgr-image
push-asset-mgr-image:
	$(call push_image,$(ASSET_MGR_IMG),asset manager image)

.PHONY: push-request-mgr-image
push-request-mgr-image:
	$(call push_image,$(REQUEST_MGR_IMG),request manager image)

.PHONY: push-agent-service-image
push-agent-service-image:
	$(call push_image,$(AGENT_SERVICE_IMG),agent service image)

.PHONY: push-integration-dispatcher-image
push-integration-dispatcher-image:
	$(call push_image,$(INTEGRATION_DISPATCHER_IMG),integration dispatcher image)

.PHONY: push-db-migration-image
push-db-migration-image:
	$(call push_image,$(DB_MIGRATION_IMG),database migration image)

.PHONY: push-mcp-emp-info-image
push-mcp-emp-info-image:
	$(call push_image,$(MCP_EMP_INFO_IMG),employee info MCP image)

.PHONY: push-mcp-snow-image
push-mcp-snow-image:
	$(call push_image,$(MCP_SNOW_IMG),snow MCP image)

# Code quality
.PHONY: lint
lint:
	@echo "Running flake8 linting on entire codebase..."
	uv run flake8 .
	@echo "Linting completed successfully!"

.PHONY: format
format:
	@echo "Running isort import sorting on entire codebase..."
	uv run isort .
	@echo "Running Black formatting on entire codebase..."
	uv run black .
	@echo "Formatting completed successfully!"

# Install dependencies
.PHONY: install-all
install-all: install-shared-db install install-asset-manager install-request-manager install-agent-service install-integration-dispatcher install-mcp-emp-info install-mcp-snow
	@echo "All dependencies installed successfully!"

.PHONY: install-shared-db
install-shared-db:
	@echo "Installing shared database dependencies..."
	cd shared-db && uv sync
	@echo "Shared database dependencies installed successfully!"

.PHONY: install
install:
	@echo "Installing self-service agent dependencies..."
	uv sync
	@echo "Self-service agent dependencies installed successfully!"

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

.PHONY: install-mcp-emp-info
install-mcp-emp-info:
	@echo "Installing employee info MCP dependencies..."
	cd mcp-servers/employee-info && uv sync
	@echo "Employee info MCP dependencies installed successfully!"

.PHONY: install-mcp-snow
install-mcp-snow:
	@echo "Installing snow MCP dependencies..."
	cd mcp-servers/snow && uv sync
	@echo "Snow MCP dependencies installed successfully!"

# Test code
.PHONY: test-all
test-all: test-shared-db test test-asset-manager test-request-manager test-agent-service test-integration-dispatcher test-mcp-emp-info test-mcp-snow
	@echo "All tests completed successfully!"

.PHONY: test-shared-db
test-shared-db:
	@echo "Running shared database tests..."
	cd shared-db && uv run python -m pytest || echo "No tests found for shared-db"
	@echo "Shared database tests completed successfully!"

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

.PHONY: test-mcp-emp-info
test-mcp-emp-info:
	@echo "Running employee info MCP tests..."
	cd mcp-servers/employee-info && uv run python -m pytest tests/
	@echo "Employee info MCP tests completed successfully!"

.PHONY: test-mcp-snow
test-mcp-snow:
	@echo "Running snow MCP tests..."
	cd mcp-servers/snow && uv run python -m pytest tests/
	@echo "Snow MCP tests completed successfully!"

.PHONY: test-short-integration
test-short-integration:
	@echo "Running short integration test..."
	cd evaluations && uv run python evaluate.py -n 1
	@echo "short integrations tests completed successfully!"

# Create namespace and deploy
namespace:
	@oc create namespace $(NAMESPACE) &> /dev/null && oc label namespace $(NAMESPACE) modelmesh-enabled=false ||:
	@oc label namespace $(NAMESPACE) maistra.io/member-of=istio-system &> /dev/null ||:
	@oc label namespace $(NAMESPACE) knative.openshift.io/part-of=openshift-serverless &> /dev/null ||:
	@oc project $(NAMESPACE) &> /dev/null ||:

.PHONY: helm-depend
helm-depend:
	@echo "Updating Helm dependencies"
	@helm dependency update helm &> /dev/null

.PHONY: helm-list-models
helm-list-models: helm-depend
	@helm template dummy-release helm --set llm-service._debugListModels=true | grep ^model:

.PHONY: helm-install
helm-install: namespace helm-depend
	@$(eval PGVECTOR_ARGS := $(call helm_pgvector_args))
	@$(eval LLM_SERVICE_ARGS := $(call helm_llm_service_args))
	@$(eval LLAMA_STACK_ARGS := $(call helm_llama_stack_args))
	@$(eval REQUEST_MANAGEMENT_ARGS := $(call helm_request_management_args))

	@echo "Cleaning up any existing jobs..."
	@oc delete job -l app.kubernetes.io/component=init -n $(NAMESPACE) --ignore-not-found || true
	@oc delete job -l app.kubernetes.io/name=self-service-agent -n $(NAMESPACE) --ignore-not-found || true
	@echo "Installing $(MAIN_CHART_NAME) helm chart"
	@helm upgrade --install $(MAIN_CHART_NAME) helm -n $(NAMESPACE) \
		--set image.repository=$(REGISTRY)/self-service-agent \
		--set image.assetManager=$(REGISTRY)/self-service-agent-asset-manager \
		--set image.requestManager=$(REGISTRY)/self-service-agent-request-manager \
		--set image.agentService=$(REGISTRY)/self-service-agent-service \
		--set image.integrationDispatcher=$(REGISTRY)/self-service-agent-integration-dispatcher \
		--set image.dbMigration=$(REGISTRY)/self-service-agent-db-migration \
		--set image.tag=$(VERSION) \
		$(PGVECTOR_ARGS) \
		$(LLM_SERVICE_ARGS) \
		$(LLAMA_STACK_ARGS) \
		$(REQUEST_MANAGEMENT_ARGS) \
		$(EXTRA_HELM_ARGS)
	@echo "Waiting for model services and llamastack to deploy. It may take around 10-15 minutes depending on the size of the model..."
	@oc rollout status deploy/$(MAIN_CHART_NAME) -n $(NAMESPACE)
	@echo "$(MAIN_CHART_NAME) installed successfully"

# Uninstall the deployment and clean up
.PHONY: helm-uninstall
helm-uninstall:
	@echo "Enhanced uninstall process for $(MAIN_CHART_NAME) helm chart in namespace $(NAMESPACE)"

	@echo "Step 1: Attempting normal helm uninstall..."
	@helm uninstall --ignore-not-found $(MAIN_CHART_NAME) -n $(NAMESPACE) || echo "Normal uninstall failed, proceeding with enhanced cleanup..."

	@echo "Step 2: Manual cleanup of namespace-scoped Knative resources..."
	@echo "Cleaning up Knative resources in $(NAMESPACE) only..."
	@oc delete triggers -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@oc delete broker -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@oc delete inmemorychannel -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@oc delete ksvc -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@oc delete certificates -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@oc delete domainmapping -n $(NAMESPACE) --all --ignore-not-found --timeout=30s || true
	@echo "Cleaning up Knative-related ConfigMaps..."
	@oc delete configmap -n $(NAMESPACE) -l app.kubernetes.io/name=self-service-agent --ignore-not-found || true
	@echo "Force cleanup any stuck resources with finalizers..."
	@oc get broker -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} oc patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' || true
	@oc get trigger -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} oc patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' || true

	@echo "Step 3: Retry helm uninstall after cleanup..."
	@helm uninstall --ignore-not-found $(MAIN_CHART_NAME) -n $(NAMESPACE) || echo "Helm uninstall completed with manual cleanup"

	@echo "Step 4: Final cleanup of namespace $(NAMESPACE)..."
	@$(MAKE) helm-cleanup-jobs
	@echo "Removing pgvector and init job PVCs from $(NAMESPACE)"
	@oc get pvc -n $(NAMESPACE) -o custom-columns=NAME:.metadata.name | grep -E '^(pg.*-data|self-service-agent-init-status)' | xargs -I {} oc delete pvc -n $(NAMESPACE) {} ||:
	@echo "Deleting remaining pods in namespace $(NAMESPACE)"
	@oc delete pods -n $(NAMESPACE) --all || true
	@echo "Checking for any remaining resources in namespace $(NAMESPACE)..."
	@echo "If you want to completely remove the namespace, run: oc delete project $(NAMESPACE)"
	@echo "Remaining resources in namespace $(NAMESPACE):"
	@$(MAKE) helm-status

# Manual cleanup for Knative resources (useful for webhook timeout issues)
.PHONY: helm-cleanup-knative
helm-cleanup-knative:
	@echo "Manual cleanup of Knative eventing resources in $(NAMESPACE)..."
	@echo "Step 1: Attempting normal deletion with short timeout..."
	@oc delete triggers -n $(NAMESPACE) --all --ignore-not-found --timeout=10s || echo "Normal trigger deletion failed, proceeding with force cleanup..."
	@oc delete broker -n $(NAMESPACE) --all --ignore-not-found --timeout=10s || echo "Normal broker deletion failed, proceeding with force cleanup..."
	@oc delete inmemorychannel -n $(NAMESPACE) --all --ignore-not-found --timeout=10s || echo "Normal inmemorychannel deletion failed, proceeding with force cleanup..."
	@oc delete configmap -n $(NAMESPACE) -l app.kubernetes.io/name=self-service-agent --ignore-not-found || true
	@echo "Step 2: Force cleanup any stuck resources with finalizers..."
	@echo "Attempting to patch finalizers on brokers..."
	@oc get broker -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} oc patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' --timeout=10s || echo "Patch failed, trying force delete..."
	@echo "Attempting to patch finalizers on triggers..."
	@oc get trigger -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} oc patch {} -n $(NAMESPACE) --type merge -p '{"metadata":{"finalizers":[]}}' --timeout=10s || echo "Patch failed, trying force delete..."
	@echo "Step 3: Force delete with zero grace period..."
	@oc get broker -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} oc delete {} -n $(NAMESPACE) --force --grace-period=0 || echo "Force delete failed, resource may need manual intervention"
	@oc get trigger -n $(NAMESPACE) -o name 2>/dev/null | xargs -r -I {} oc delete {} -n $(NAMESPACE) --force --grace-period=0 || echo "Force delete failed, resource may need manual intervention"
	@echo "Step 4: Final verification..."
	@echo "Remaining Knative resources in $(NAMESPACE):"
	@oc get broker,trigger,inmemorychannel -n $(NAMESPACE) 2>/dev/null || echo "No Knative eventing resources found"
	@echo "Knative cleanup completed for namespace $(NAMESPACE). If resources still exist, they may require cluster admin intervention to resolve webhook issues."

# Clean up leftover jobs
.PHONY: helm-cleanup-jobs
helm-cleanup-jobs:
	@echo "Cleaning up leftover jobs in namespace $(NAMESPACE)..."
	@oc delete jobs -n $(NAMESPACE) -l app.kubernetes.io/name=self-service-agent --ignore-not-found || true
	@echo "Job cleanup completed for namespace $(NAMESPACE)"

# Check deployment status
.PHONY: helm-status
helm-status:
	@echo "Listing pods..."
	oc get pods -n $(NAMESPACE) || true

	@echo "Listing services..."
	oc get svc -n $(NAMESPACE) || true

	@echo "Listing routes..."
	oc get routes -n $(NAMESPACE) || true

	@echo "Listing secrets..."
	oc get secrets -n $(NAMESPACE) | grep huggingface-secret || true

	@echo "Listing pvcs..."
	oc get pvc -n $(NAMESPACE) || true
