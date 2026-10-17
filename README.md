# FluentPet Connect for Home Assistant

A Home Assistant integration for [FluentPet Connect](https://fluent.pet).

> **Under construction.** This repository holds the scaffold only. The
> integration code is not written yet and nothing here is ready to install.

## What it will do

- Sign in with your FluentPet account through OAuth2, from the Home Assistant
  integrations page.
- Receive button presses from your household's FluentPet Connect buttons as
  Home Assistant events, so automations can react to what a learner says.
- Surface interactions recorded in the household.
- Show whether each FluentPet Connect Base is connected.

Updates arrive by push: the integration registers a FluentPet household
webhook and reads the household event feed (FluentPet api/v3), so no polling
is involved.

The integration is built to Home Assistant core standards, first as a HACS
custom integration and later as a submission to Home Assistant core. The API
client will be published as a separate Python library on PyPI.

## Installation (planned)

Once a release is available:

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/CleverPet/fluentpet-homeassistant` with category
   **Integration**.
3. Install **FluentPet Connect** and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration** and choose
   **FluentPet Connect**.

Home Assistant 2026.10.0 or newer is required.

## Development

Requirements: Python 3.14 (Home Assistant requires 3.14.2 or newer).

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
```

Without uv: `python3.14 -m venv .venv` and
`.venv/bin/pip install -r requirements_test.txt`.

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

## License

Apache License 2.0. See [LICENSE](LICENSE).
