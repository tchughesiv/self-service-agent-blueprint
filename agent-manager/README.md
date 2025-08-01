# Self-service agent blueprint: Agent Manager Module

## Project sync

To alig `.venv` environment with any change applied to the project.

```shell
uv sync --all-packages
```

## Run unit tests

```shell
uv run pytest
```

## Configure the IDE

Select the `Venv` environment `.venv` under the `agent-manager`.

Note: this is usually done automatically by the IDE if the `agent-manager` directory
is opened as root project.

## Play with the App

1. Run Llama Stack

To run it locally see [local_llamastack_server](local_llamastack_server/README.md)

2. Run the play app

```shell
uv run script/play.py
```

## Build the container

```shell
podman build -t backend-app .
```