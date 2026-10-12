# Changelog

Notable changes to KNX Smart Home are recorded here. Entries under Unreleased
are available on the default branch and are not a published release guarantee.

## Unreleased

### Added

- Python automation agent with SQLite registry, role-based access, and device skills.
- Telegram and Zalo gateway, Google Sheets synchronization, and MQTT-to-KNX bridge.
- Homebridge MQTT integration, experimental direct KNX plugin, and optional OpenClaw tools.
- English documentation, contribution guidelines, issue forms, and pull request template.
- Automated unit tests, source validation, documentation checks, and Git-history secret scanning.
- Isolated API initialization smoke check for development and CI.

### Changed

- Author and maintainer attribution to khoudang.

- Project identity and repository name to KNX Smart Home.

### Known limitations

- Direct Homebridge accessory support is incomplete.
- Agent-driven scene execution is disabled.
- Runtime dependencies are not fully pinned across all components.
- Hardware compatibility requires installation-specific testing.
