# Self-service agent blueprint: backend application

## Build and run from a container

```shell
podman build -t backend-app .
```

```shell
podman run -p 8000:8000 backend-app
```

## Build and run locally

```shell
uv sync --all-packages
```

```shell
uv run uvicorn main:app --port 8000
```

## Build and run from IDE

On Cursor / VSCode go to the `Python` extension.

Select the `Venv` environment `.venv` under the `backend`.  