# Self-Service Agent Blueprint

A comprehensive blueprint for building self-service AI agents with modular components including asset management, employee information services, and Helm-based deployment.

## Project Structure

- **`asset-manager/`** - Asset management module for agents and knowledge bases
- **`request-manager/`** - Request Management Layer for handling multi-integration requests
- **`agent-service/`** - CloudEvent-driven agent service for processing requests
- **`mcp-servers/employee-info/`** - Employee information MCP server
- **`mcp-servers/snow/`** - ServiceNow integration MCP server
- **`helm/`** - Helm charts for Kubernetes deployment with Knative and Service Mesh configurations
- **`examples/`** - Example scripts and demonstrations
- **`test/`** - Root project tests

## Quick Start

### Prerequisites

- **Python 3.12+** - Required for all services and components
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer
- [Podman](https://podman.io/) or Docker - Container runtime
- [Helm](https://helm.sh/) - Kubernetes package manager (for deployment)

### Installation

Install all project dependencies:

```bash
make install-all
```

Or install specific components:

```bash
make install                    # Self-service agent dependencies
make install-asset-manager      # Asset manager dependencies
make install-request-manager    # Request manager dependencies
make install-agent-service      # Agent service dependencies
make install-mcp-emp-info       # Employee info MCP dependencies
make install-mcp-snow          # ServiceNow MCP dependencies
```

## Development Commands

### Code Quality

```bash
make lint                       # Run flake8 linting on entire codebase
make format                     # Run Black formatting on entire codebase
```

### Testing

```bash
make test-all                   # Run tests for all projects
make test                       # Run tests for self-service agent
make test-asset-manager         # Run tests for asset manager
make test-request-manager       # Run tests for request manager
make test-agent-service         # Run tests for agent service
make test-mcp-emp-info          # Run tests for employee info MCP
make test-mcp-snow             # Run tests for ServiceNow MCP
```

### Container Operations

> **Note**: Container images do not include HEALTHCHECK instructions as health monitoring is handled by Kubernetes liveness and readiness probes in the Helm deployment.

#### Building Images

```bash
make build-all-images           # Build all container images
make build-agent-image          # Build self-service agent image
make build-asset-mgr-image      # Build asset manager image
make build-request-mgr-image    # Build request manager image
make build-agent-service-image  # Build agent service image
make build-mcp-emp-info-image   # Build employee info MCP image
make build-mcp-snow-image       # Build ServiceNow MCP image
```

#### Pushing Images

```bash
make push-all-images            # Push all images to registry
make push-agent-image           # Push self-service agent image
make push-asset-mgr-image       # Push asset manager image
make push-request-mgr-image     # Push request manager image
make push-agent-service-image   # Push agent service image
make push-mcp-emp-info-image    # Push employee info MCP image
make push-mcp-snow-image        # Push ServiceNow MCP image
```

## Request Management Layer

This blueprint includes a comprehensive Request Management Layer that enables multi-integration support and event-driven architecture. See [REQUEST_MANAGEMENT.md](REQUEST_MANAGEMENT.md) for detailed documentation.

### Key Features

- **Multi-Integration Support**: Slack, Web, CLI, and Tool integrations
- **Event-Driven Architecture**: Uses OpenShift Serverless (Knative) with CloudEvents
- **Service Mesh Security**: OpenShift Service Mesh (Istio) for authentication, authorization, and traffic management
- **Session Management**: Persistent conversation state across interactions
- **Request Normalization**: Unified format for all integration types
- **Scalable Processing**: Auto-scaling Knative services with advanced observability

### Quick Demo

```bash
# Run the request management demo
python examples/request-management-demo.py
```

### Deployment

#### Helm Operations

```bash
make helm-depend                # Update Helm dependencies
make helm-list-models           # List available models
```

#### Deploy to Kubernetes/OpenShift

```bash
# Set your namespace
export NAMESPACE=your-namespace

# Install the complete stack
make helm-install

# Check deployment status
make helm-status

# Uninstall
make helm-uninstall
```

## Configuration

### Container Registry

By default, images are built for `quay.io/ecosystem-appeng`. To use a different registry:

```bash
export REGISTRY=your-registry.com/your-org
make build-all-images
```

### Version Management

To build with a specific version:

```bash
export VERSION=1.0.0
make build-all-images
```

## Available Make Targets

Run `make help` to see all available commands with descriptions.

## Component Documentation

- [Asset Manager](asset-manager/README.md) - Detailed asset manager documentation
- [Employee Info MCP](mcp-servers/employee-info/README.md) - Employee information service documentation