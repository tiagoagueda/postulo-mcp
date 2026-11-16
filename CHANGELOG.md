# Changelog

All notable changes to postulo-mcp are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A version's first two numbers are
the Postulo it is released beside (0.5.x is for Postulo 0.5); the patch number is this
repository's own.

## [Unreleased]

## [0.5.1] - 2026-11-16

### Added

- `whoami` and `preview_posting` read tools, and a `captures` write kind with `capture_posting`, so an agent that finds a job advert can show what would be captured and hand it over (#1)
- Postulo's own logo, with a NOTICE saying where it comes from and that the name and logo
  are not under the AGPL (#2)
- A changelog, a release workflow that refuses a tag disagreeing with `pyproject.toml` or
  this file and publishes a Forgejo release with the wheel and sdist, a weekly audit of the
  locked dependencies, a security policy, and a README that says how to install a released
  version, what "official" means for a companion tool and which Postulo each version works
  with (#1)

## [0.5.0] - 2026-11-01

### Added

- Tested against the API of Postulo 0.5.0 (#24)

### Fixed

- Security review fixes: one fence for what strangers wrote and for what the instance
  answered, honest API calls, a bounded and throttled port, CI that installs locked and
  runs on Python 3.12 to 3.14 (#22)
- Locked `certifi` moved past PYSEC-2026-4320 (#23)

### Changed

- English only, by decision, and said so in the README (#5)
