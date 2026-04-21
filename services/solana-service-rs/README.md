# Solana Service (Rust)

On-chain transaction building and Solana DeFi protocol integration.

## Responsibilities

- Swap transaction building (Jupiter aggregator)
- Token transfer transaction construction
- Staking operations (Marinade, Jito, BlazeStake)
- Quote fetching with price impact analysis
- Protocol and token metadata
- Transaction simulation and submission

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/actions/quote` | Get swap/transfer quote |
| POST | `/actions/build` | Build unsigned transaction |
| POST | `/actions/submit` | Submit signed transaction |
| GET | `/protocols` | List supported protocols |
| GET | `/tokens` | List known tokens |
| GET | `/tokens/:mint` | Get token info |

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | HTTP port (default: 3030) |
| `GRPC_PORT` | No | gRPC port (default: 50053) |
| `OPRAI_JWT_SECRET` | Yes | JWT validation |
| `OPRAI_INTERNAL_API_KEY` | Yes | Service-to-service auth |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SOLANA_RPC` | No | Solana RPC endpoint (default: mainnet public) |
| `SOLANA_NETWORK` | No | `mainnet-beta` or `devnet` |

## Database Schema

- **Schema**: `solana_schema`
- **Tables**: `transactions`

## Run

```bash
# Dev (native)
cd services/solana-service-rs && cargo run

# Docker
docker compose -f infra/docker-compose.yml up solana-service-rs

# Build (release)
cd services/solana-service-rs && cargo build --release

# Tests
cd services/solana-service-rs && cargo test
```

## Project Structure

```
solana-service-rs/
├── src/
│   ├── main.rs              Actix-Web server entry point
│   ├── config.rs             Configuration
│   ├── handlers/             HTTP route handlers
│   ├── services/             Business logic
│   ├── models/               Data models
│   ├── grpc/                 gRPC server (Tonic)
│   └── solana/               Solana SDK integration
├── build.rs                  Proto stub generation (tonic-build)
├── examples/                 Usage examples
├── Cargo.toml
├── Cargo.lock
└── Dockerfile
```

## Notes

- First build downloads and compiles all Rust crates (3-5 minutes)
- Proto stubs are generated at build time via `build.rs` + `tonic-build`
- Uses `solana-sdk` for transaction construction and `spl-token` for SPL operations
