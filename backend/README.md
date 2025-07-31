# Self-service agent blueprint: backend application

## Build and run from a container {#container-run}

```shell
podman build -t backend-app .
```

```shell
podman run -p 8000:8000 backend-app
```

## Build and run locally {#local-run}

```shell
uv sync --all-packages
```

```shell
uv run uvicorn main:app --port 8000
```

## Build and run from IDE

On Cursor / VSCode go to the `Python` extension.

Select the `Venv` environment `.venv` under the `backend`.  

## Run unit tests

```shell
uv run pytest
```

## Play with the Backend App

1. Run Llama Stack locally

See [local_llamastack_server](local_llamastack_server)

2. Run the Backend app (locally or with a container)

See [container-run](#container-run) or [local-run](#local-run)

3. Try some REST commands to interact with the agent service

See [rest-commands](#rest-commands)

### REST commands {#rest-commands}

In this example I'm using [Httpie](https://httpie.io/)

* To connect to Llama Stack:

```shell
http post http://127.0.0.1:8000/connect
```

* To verify if the service is connected:

```shell
http http://127.0.0.1:8000/connect
```

Note: get is the default verb used by Httpie.

* To register the agents:

```shell
http post http://127.0.0.1:8000/agents
```

* To get the list of the agents:

```shell
http http://127.0.0.1:8000/agents
```
