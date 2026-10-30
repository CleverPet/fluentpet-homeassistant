# Releasing

How changes reach `main` and how a release is made. For the development setup
and checks, see [Development](development.md).

## Branches and pull requests

- The repository is trunk-only: `main` is the only long-lived branch.
- Work happens on short-lived branches. Each pull request is squash merged into
  `main` with a plain summary of the change.
- The required checks on `main` are `Lint, types and tests` and `Hassfest`.

## Changelog

`CHANGELOG.md` keeps an `Unreleased` section at the top. Each pull request with
a user-facing change adds its entry there.

## Making a release

1. Move the entries under `Unreleased` in `CHANGELOG.md` to a new version
   heading.
2. Bump `version` in `custom_components/fluentpet/manifest.json` to the same
   version.
3. Merge that change into `main`.
4. Tag the commit on `main` with a semver tag `vX.Y.Z`.
5. Publish a GitHub Release for the tag, with the notes for that version from
   `CHANGELOG.md`.

HACS installs from releases, not from `main`.
