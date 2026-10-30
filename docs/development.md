# Development

How to set up a development environment, run the checks, and work on the
FluentPet API client library. For the branch and release process, see
[Releasing](releasing.md).

## Setup

Requirements: Python 3.14 (Home Assistant requires 3.14.2 or newer).

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
```

Without uv: `python3.14 -m venv .venv` and
`.venv/bin/pip install -r requirements_test.txt`.

## Checks

Checks (the same as CI):

```sh
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest
```

`hassfest` and HACS validation run in GitHub Actions
(`.github/workflows/validate.yml`) on every push to `main` and on pull
requests. To run hassfest locally:

```sh
docker run --rm -v "$PWD":/github/workspace ghcr.io/home-assistant/hassfest
```

The lint, type and test checks run in `.github/workflows/test.yml` as the job
`Lint, types and tests`, on the same triggers.

## API client library

The FluentPet API client is vendored at
`custom_components/fluentpet/fluentpet_api` and imported as
`from .fluentpet_api import ...`. It depends only on `aiohttp` and never imports
Home Assistant. `libs/fluentpet-api` holds the packaging
metadata for the future PyPI package `fluentpet-api`; nothing is published from
there yet. Its tests are in `tests/fluentpet_api`. See
[libs/fluentpet-api/README.md](../libs/fluentpet-api/README.md).
