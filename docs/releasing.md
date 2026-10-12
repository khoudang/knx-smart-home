# Release process

Project releases are created from reviewed commits on the default branch. Package
metadata is independent of project release tags; do not present an unreleased
snapshot as a verified production release.

## Release criteria

1. Check out the candidate commit in a clean environment.
2. Verify CI, unit tests, documentation links, API smoke checks, and secret scanning.
3. Install the components being released from their documented dependency files.
4. Test changed integrations against their target runtimes; record hardware coverage
   explicitly and identify components excluded from the release.
5. Review configuration changes, dependency changes, licenses, and migration requirements.
6. Update the changelog, move applicable Unreleased entries into a dated version section,
   and record known limitations.
7. Create a version tag and GitHub release with installation, compatibility, and rollback notes.

Do not bundle environment files, credentials, caches, databases, logs, or customer
configuration in release artifacts. A Homebridge npm publication requires its own
package review and validation; a source repository release does not publish npm packages.

## Versioning

Use `vMAJOR.MINOR.PATCH` project tags once releases begin. Document breaking changes
and database migrations explicitly. Use prerelease suffixes for candidates whose
integration validation is incomplete. Retain the previous deployment and a database
backup until the new release has been verified.
