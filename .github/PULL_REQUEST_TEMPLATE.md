## Summary

<!-- What changed? Keep this concise and user-focused. -->

## Why

<!-- What problem does this solve? Link an issue with `Closes #...` when applicable. -->

## Changes

- Describe the main implementation change.

## Validation

<!-- List the exact checks you ran and their results. -->

- [ ] Python tests pass: `python -m pytest -q`
- [ ] Desktop tests pass when affected: `cd desktop && npm test`
- [ ] I manually verified the relevant user workflow when appropriate.

## Compatibility and trust boundaries

- [ ] Structural analysis remains local by default.
- [ ] Any new network access, persisted data, credential handling, or provider behavior is documented above.
- [ ] Logs, fixtures, screenshots, and test data contain no credentials, customer data, or private contracts.
- [ ] Breaking changes and migration requirements are documented above.
- [ ] New dependencies and applicable third-party notices are included.

## Scope

- [ ] This change is focused on Ionic Essential and contains no generated build artifacts or commercial-only control-plane features.
- [ ] Behavioral changes include appropriate tests.
- [ ] I agree that this contribution is provided under the repository's MIT License.
