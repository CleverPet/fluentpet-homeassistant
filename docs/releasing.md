# Releasing

How changes reach `main` and how a release is made. For the development setup
and checks, see [Development](development.md).

## Branches and pull requests

- The repository is trunk-only: `main` is the only long-lived branch.
- Work happens on short-lived branches. Each pull request is squash merged into
  `main` with a plain summary of the change as the commit message.
- The required checks on `main` are `Lint, types and tests` and `Hassfest`.

## Changelog

`CHANGELOG.md` keeps an `Unreleased` section at the top. Each pull request with
a user-facing change adds its entry there.

A release moves those entries under a version heading. The heading line is
exactly `## X.Y.Z`, with no `v` and no date, for example `## 1.2.0`. The
release workflow reads the text between that heading and the next `## `
heading as the release notes, and refuses a release whose section is missing
or empty.

## Versions

Versions follow semver, `X.Y.Z`, with numbers without leading zeros and no
prerelease or build suffix. The tag of a release is `vX.Y.Z`. The version is
kept in two files, which must both equal `X.Y.Z` in the released commit:

- `version` in `custom_components/fluentpet/manifest.json`.
- `version` under `[project]` in `pyproject.toml`.

## Making a release

1. Open a release pull request that:
   - moves the entries under `## Unreleased` in `CHANGELOG.md` to a new
     `## X.Y.Z` section below it, and leaves `## Unreleased` in place, empty;
   - sets `version` in `custom_components/fluentpet/manifest.json` and in
     `pyproject.toml` to `X.Y.Z`.

   Check it locally with `.venv/bin/python script/release.py check vX.Y.Z`,
   and preview the notes with `.venv/bin/python script/release.py notes X.Y.Z`.
2. Squash merge the release pull request into `main`.
3. Tag the merge commit with an annotated tag and push the tag:

   ```sh
   git switch main
   git pull --ff-only
   git log -1 --oneline   # the merge commit of the release pull request
   git tag -a vX.Y.Z -m "X.Y.Z"
   git push origin vX.Y.Z
   ```

4. The push starts the `Release` workflow
   (`.github/workflows/release.yml`). It publishes the GitHub release; do not
   create one by hand.

### What the release workflow does

The job `Check release` checks out the tag and stops the release when:

- the tag is not of the form `vX.Y.Z`;
- the manifest or `pyproject.toml` version is not `X.Y.Z`;
- `CHANGELOG.md` has no `## X.Y.Z` section, or the section is empty;
- the tag is not an annotated tag;
- the tagged commit is not on `main`;
- a release for the tag already exists.

The workflow then runs the `Test` (`Lint, types and tests`) and `Validate`
(`Hassfest`, `HACS`) workflows on the tagged commit. When all of them pass,
the job `Publish release`:

1. builds `fluentpet.zip` with the contents of `custom_components/fluentpet`
   at the root of the archive (`manifest.json` at the top level, without
   `__pycache__` or `.pyc` files);
2. writes the `## X.Y.Z` section of `CHANGELOG.md` as the release notes;
3. creates the GitHub release `X.Y.Z` for the tag, with those notes and the
   `fluentpet.zip` asset, marked as the latest release.

### Running the workflow again

To run the workflow for an existing tag, for example after a transient
failure, open **Actions > Release > Run workflow**, choose **Use workflow
from: Tags: vX.Y.Z**, and enter `vX.Y.Z` as the tag. The workflow refuses to
run from a branch, so every job checks the tagged commit. It also refuses
when a release for the tag already exists.

### A failed release

The release is only created in the last step, so a run that fails before the
`Publish release` job leaves no release. To fix the cause and release the same
version:

1. Merge the fix into `main`, through a pull request as usual.
2. Delete the tag locally and on GitHub, then tag the new commit and push the
   tag again:

   ```sh
   git push origin --delete vX.Y.Z
   git tag -d vX.Y.Z
   git switch main
   git pull --ff-only
   git tag -a vX.Y.Z -m "X.Y.Z"
   git push origin vX.Y.Z
   ```

Once a release is published, do not move or delete its tag; release a new
version instead.

## HACS

`hacs.json` sets `zip_release` and `filename`, so HACS installs the
`fluentpet.zip` asset of a release and extracts it into
`custom_components/fluentpet`. HACS shows the tag of the latest release as the
version and installs that release by default.

With `zip_release`, HACS downloads `fluentpet.zip` from the release of the
selected version, so it installs releases only. `hide_default_branch` removes
the `main` branch from the versions HACS offers, because `main` has no release
asset. Installing through HACS before the first release exists fails. To test
an unreleased change, install it manually from a checkout of the branch.
