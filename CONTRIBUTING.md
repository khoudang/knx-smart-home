# Contributing

Thank you for contributing to KNX Smart Home, developed by AICONTROL.

## Before making changes

Use issues for reproducible bugs and focused feature proposals. Read the
[architecture](docs/architecture.md), [compatibility notes](docs/compatibility.md),
and [security policy](SECURITY.md). Report vulnerabilities privately.

Use English for public documentation, issues, and pull requests. Runtime language
support is separate from documentation language; do not translate user-facing
responses as an incidental change.

## Development setup

Follow the [quick start](README.md#quick-start). The core unit suite uses the Python
standard library; runtime and integration dependencies are installed separately.
Use Node.js 22 and Python 3.12 to match the main CI environment.

Create a branch for a focused change. Keep generated runtime files, credentials,
customer data, and hardware configuration out of commits.

## Validation

```bash
python -m unittest discover -s tests
python scripts/check_docs.py
python scripts/check_public_tree.py
git diff --check
```

Stage new files before running the publication checker. For changed JavaScript
or Bash files, run `node --check path/to/file.js` or `bash -n path/to/file.sh`.
Use `python scripts/smoke_api.py` after installing agent requirements to verify
API initialization without credentials or hardware.

Add behavioral tests for meaningful logic changes. Use mocks and synthetic registry
fixtures. Hardware tests must remain explicit, opt-in, and outside routine CI.
Document changes to environment variables, database behavior, or integration schemas.

## Pull requests

Explain the problem, the resulting behavior, and how it was validated. Link the
relevant issue when one exists. Keep unrelated refactors separate and include
migration or rollback notes for changes affecting existing installations.

Use descriptive commit messages. Do not introduce new dependencies without explaining
why they are needed. Add notable user-facing changes to `CHANGELOG.md` under Unreleased.

## Licensing and conduct

Contributions are accepted under the license of the component being changed.
Preserve existing copyright and third-party notices. Discuss changes respectfully,
focus review on the code, and avoid publishing personal or customer information.
