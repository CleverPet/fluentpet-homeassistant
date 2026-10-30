# Agent instructions

Rules for coding agents working in this repository. Setup and checks are in
[docs/development.md](docs/development.md); the branch and release process is
in [docs/releasing.md](docs/releasing.md).

## Public repository

This repository is public. Files, commit messages, branch names and pull
requests must not contain:

- Ticket keys or other issue-tracker references.
- Links to private repositories.
- Internal environment names or hostnames.
- Deployment, operator or secrets steps.

## FluentPet hosts

The integration uses FluentPet's production hosts only: the API host
`DEFAULT_BASE_URL` in `custom_components/fluentpet/fluentpet_api/const.py`, and
the sign-in and account hosts in `custom_components/fluentpet/const.py`. Never
commit any other FluentPet host, including as a default, placeholder or example
for the advanced API base URL option. Tests that need another host use reserved
example domains such as `example.com`.

## Secrets and tests

- No secrets, tokens or credentials in code, tests, fixtures or snapshots.
- Tests never reach the network.

## Environment and checks

- Use a `.venv` with Python 3.14 in the repository root. Create it and install
  `requirements_test.txt` as in
  [docs/development.md](docs/development.md#setup).
- Run `ruff format --check .`, `ruff check .`, `mypy` and `pytest` from `.venv`
  before you hand in a change.
- hassfest and HACS validation run in CI (`.github/workflows/validate.yml`).
  When you change `manifest.json`, `strings.json` or `quality_scale.yaml`, run
  hassfest locally as in [docs/development.md](docs/development.md#checks).

## Home Assistant conventions

Follow Home Assistant core conventions:

- Setup through the config flow. `"config_flow": true` in `manifest.json` must
  ship with `config_flow.py`.
- Keep per-entry state in `entry.runtime_data`.
- Entities use `has_entity_name` and translation keys. User-facing strings live
  in `strings.json` and `translations/en.json`, kept in sync.
- Keep `quality_scale.yaml` up to date with the rules the integration meets.
- The API client lives behind a thin module boundary in `fluentpet_api`. It
  never imports Home Assistant.

## Branches and releases

- Work on a short-lived branch whose name has no ticket key. Pull requests are
  squash merged into `main`.
- Every user-facing change adds an entry under `Unreleased` in `CHANGELOG.md`.
- Agents never create or push tags and never publish GitHub Releases.

## Copy style

- In user-facing copy, say "learner", not "pet", for the animal.
- No em dashes.
