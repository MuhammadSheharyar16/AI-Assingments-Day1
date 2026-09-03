# Day 6 `uv` Workflow

`uv` is the required package and environment manager for Day 6.

## Required root files

```text
pyproject.toml
uv.lock
.gitignore
```

The local environment is created/managed by `uv`:

```text
.venv/
```

`.venv/` must be ignored by Git and excluded from the submission.

## Setup

From the repository root:

```powershell
uv sync
```

## Add dependencies

Use `uv add`, not manual `pip install` commands:

```powershell
uv add fastapi
uv add "uvicorn[standard]"
uv add opentelemetry-api opentelemetry-sdk
uv add --dev pytest pytest-asyncio httpx
```

Only add packages actually required by your implementation.

## Run tests

```powershell
uv run pytest -q
```

## Run the API

Use the actual import path implemented by your repository. For the required structure in the assignment, the expected shape is:

```powershell
uv run uvicorn aico.api.app:app --reload
```

## Dependency source of truth

- `pyproject.toml` is the dependency/project configuration source of truth.
- `uv.lock` is the reproducible lockfile and must be committed.
- Do not manually edit `uv.lock`.
- Do not use `requirements.txt` as the active Day 6 dependency workflow.
- If a legacy `requirements.txt` remains for a documented external compatibility reason, the project must still install and run from `pyproject.toml` + `uv.lock` using `uv sync`.
- README commands must use `uv sync` and `uv run ...`.

## Reviewer expectation

A clean reviewer environment should be able to run:

```powershell
uv sync
uv run pytest -q
```

without undocumented manual `pip install` steps.
