# Contributing to OPRAI

Thank you for your interest in contributing to OPRAI.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/your-username/oprai.git`
3. Set up the environment: `cp .env.example .env` then fill in your values
4. Install dependencies: `make install`
5. Start infrastructure: `make dev-infra`
6. Run migrations: `make migrate`
7. Start all services: `make dev-all`

## Development Workflow

```bash
git checkout -b feat/your-feature   # create a branch
# make your changes
make test                            # run tests
git commit -m "feat: description"    # commit
git push origin feat/your-feature   # push
# open a pull request
```

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feat/short-description` | `feat/swap-slippage-ui` |
| Bug fix | `fix/short-description` | `fix/jwt-expiry-handling` |
| Refactor | `refactor/short-description` | `refactor/gateway-middleware` |
| Docs | `docs/short-description` | `docs/api-reference` |

## Commit Style

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Jupiter V6 swap support
fix: handle empty wallet response in portfolio
refactor: extract token resolver into shared util
docs: update env variable reference
test: add integration tests for auth flow
```

## Code Style

| Language | Tool | Command |
|----------|------|---------|
| Go | `gofmt` + `go vet` | `make lint-go` |
| Python | `ruff` | `make lint-python` |
| Rust | `rustfmt` + `clippy` | `make lint-rust` |
| TypeScript | `eslint` | `pnpm lint` |

## Testing

- Write tests for new features and bug fixes
- All tests must pass before a PR can be merged
- For Rust financial calculations: use `rust_decimal`, never raw `f64` arithmetic
- Integration tests: use environment variables, never hardcode credentials

## Pull Request Guidelines

- Keep PRs focused — one feature or fix per PR
- Include a description of what changed and why
- Link related issues with `Closes #123`
- Ensure CI passes before requesting review

## Security

If you find a security vulnerability, **do not open a public issue**. See [SECURITY.md](./SECURITY.md) for the responsible disclosure process.

## Architecture Overview

See [README.md](./README.md) for the full architecture. Key principles:

- Inter-service communication via gRPC + Protobuf
- All secrets via environment variables — never hardcoded
- Per-service schema isolation in PostgreSQL
- JWT validated at the gateway — services trust `X-User-Wallet` header

## Questions

Open a [GitHub Discussion](https://github.com/oprai/oprai/discussions) for questions that are not bug reports or feature requests.
