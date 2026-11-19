# Contributing

## Setup

```bash
make install                      # uv sync and pnpm install
createdb switchboard_test         # integration tests need Postgres and Redis
make test
```

Integration tests skip themselves when Postgres or Redis isn't reachable. They use the
`switchboard_test` database and Redis database 15; override with `SWITCHBOARD_TEST_DATABASE_URL`
and `SWITCHBOARD_TEST_REDIS_URL`.

## Before opening a pull request

```bash
make lint      # ruff format --check, ruff check, mypy --strict, dashboard lint and typecheck
make test
```

CI runs the same checks, builds the dashboard and the Docker image, and runs the smoke eval.

## Branching

- `main` holds released code and is tagged `vX.Y.Z`.
- `develop` is where finished work lands.
- Branch from `develop` as `feature/<short-name>`, and open a pull request back into `develop`.
- Releases branch from `develop` as `release/X.Y.Z`, are merged into `main`, tagged, and merged
  back into `develop`.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat(scope): ...`, `fix: ...`,
`test: ...`, `docs: ...`, `refactor: ...`, `build: ...`, `ci: ...`, `chore: ...`. Imperative mood,
lowercase after the colon, no trailing period. Keep commits small enough that each one explains
itself.

## Code

- Match the surrounding code. Comments explain why, not what.
- New behaviour comes with a test that fails without it. For concurrency and failure handling,
  prefer a test that reproduces the failure (a real server, a killed connection) over a mock.
- Anything that talks to a model provider must keep working when that provider is slow or down;
  check how the change behaves on timeouts and partial streams.
- Database changes need an Alembic migration in `migrations/versions/`.
- UI changes should be checked in light and dark themes and at phone width.
