# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Release notes are generated from
[conventional commits](https://www.conventionalcommits.org/en/v1.0.0/), so a
`fix:` prefix in a commit subject is what puts a line under **Fixed**.

## [Unreleased]

### Added
- Repo privacy gate (`python -m tools.privacy_scan`) with offline canary tests
  proving it flags credentials, personal paths, shipping ZIPs, and machine
  models without flagging ordinary code.
- AST-based check that every import is stdlib or first-party, keeping the tool
  dependency-free.
- Packaging metadata (`pyproject.toml`) and an `ebay-deals` console script.
- `CHANGELOG.md` and conventional-commit release automation.

### Fixed
- Browse OAuth sends `Authorization: Basic` instead of credentials in the form
  body, which eBay rejects with `invalid_grant`.
- `getItem` normalizes bare legacy IDs to the full `v1|id|suffix` form the
  Browse API requires.
- Record mapping for Browse API price, shipping, condition, and seller fields.
- Variation-table parsing, out-of-stock floor handling, delivered-price maths,
  and rejection of 13-digit promotion cards.
- Ranker hyphen handling (`14-Core`, `64-GB`), `--state`, cache directory
  creation, OAuth error reporting, and persistence of search-phase blocks.

## [0.1.0]

Initial release.
