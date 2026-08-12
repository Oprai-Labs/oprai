# Solana Service (Rust)

Transaction builder service for Solana operations. Provides swap, stake, transfer, NFT, and cross-chain operations via Actix-Web HTTP + Tonic gRPC.

## Quick Start

```bash
cd services/solana-service-rs
cargo run --release
# → HTTP: http://localhost:3030
# → gRPC: localhost:50053
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │        SOLANA SERVICE (Rust)        │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │   Actix-Web Server (:3030)  │    │
                                    │  │   Actions + Protocol Routes │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │    Tonic gRPC (:50053)      │    │
                                    │  │     HealthService           │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────────────────────────────────┼───────────────────┐
         │                                            │                   │
         ▼                                            ▼                   ▼
    ┌─────────────┐                           ┌─────────────┐       ┌─────────────┐
    │ PostgreSQL  │                           │ Solana RPC  │       │   Jupiter   │
    │solana_schema│                           │  (Multi)    │       │    API      │
    └─────────────┘                           └─────────────┘       └─────────────┘
```

---

## File Structure

```
services/solana-service-rs/
├── Cargo.toml
├── build.rs                       # Tonic proto compilation
│
├── src/
│   ├── main.rs                    # Entry point (Actix + gRPC spawn)
│   ├── config.rs                  # Environment config
│   ├── error.rs                   # AppError enum
│   │
│   ├── db/
│   │   ├── connection.rs          # Diesel async pool
│   │   ├── models.rs              # Transaction model
│   │   └── schema.rs              # Diesel schema (transactions table)
│   │
│   ├── middleware/
│   │   └── auth.rs                # X-Internal-Api-Key validation
│   │
│   ├── routes/
│   │   ├── actions.rs             # /actions/* endpoints
│   │   ├── protocols.rs           # /protocols/* endpoints
│   │   └── health.rs              # /health endpoint
│   │
│   ├── services/                  # Protocol implementations
│   │   ├── mod.rs
│   │   ├── builder.rs             # Action builder router
│   │   ├── swap.rs                # Jupiter swap
│   │   ├── transfer.rs            # SOL/SPL token transfer
│   │   ├── burn.rs                # Token burn
│   │   ├── dca.rs                 # Jupiter DCA orders
│   │   ├── limit_order.rs         # Jupiter limit orders
│   │   ├── simulation.rs          # TX simulation
│   │   ├── jupiter_lend.rs        # Jupiter lending
│   │   ├── jupiter_perp.rs        # Jupiter perps
│   │   ├── marinade.rs            # Marinade liquid staking
│   │   ├── jito.rs                # Jito staking + bundles
│   │   ├── jupsol.rs              # JupSOL staking
│   │   ├── orca.rs                # Orca CLMM
│   │   ├── raydium.rs             # Raydium AMM
│   │   ├── meteora.rs             # Meteora DLMM
│   │   ├── drift.rs               # Drift perps
│   │   ├── kamino.rs              # Kamino lending
│   │   ├── blazestake.rs          # BlazeStake LST
│   │   ├── pumpfun.rs             # Pump.fun token launch
│   │   ├── magic_eden.rs          # Magic Eden NFT
│   │   ├── tensor.rs              # Tensor NFT
│   │   ├── debridge.rs            # deBridge cross-chain
│   │   ├── squid.rs               # Squid Router
│   │   ├── relay.rs               # Relay cross-chain
│   │   └── streamflow.rs          # Streamflow vesting
│   │
│   ├── solana/
│   │   ├── mod.rs
│   │   ├── connection.rs          # SolanaRpc wrapper
│   │   ├── multi_rpc.rs           # Multi-RPC fallback
│   │   └── tokens.rs              # COMMON_TOKENS registry
│   │
│   └── grpc/
│       ├── mod.rs
│       └── health.rs              # HealthService implementation
│
└── examples/
    └── validate_all.rs            # Validation tests
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `3030` | HTTP port |
| `GRPC_PORT` | `50053` | gRPC port |
| `DATABASE_URL` | Auto-composed | PostgreSQL connection |
| `SOLANA_RPC` | `https://api.mainnet-beta.solana.com` | Primary RPC |
| `SOLANA_RPC_FALLBACK` | *(empty)* | Comma-separated fallback RPCs |
| `SOLANA_NETWORK` | `mainnet-beta` | Network name |
| `OPRAI_INTERNAL_API_KEY` | *(required in prod)* | Inter-service auth |
| `DB_SCHEMA` | `solana_schema` | PostgreSQL schema |
| `NODE_ENV` | `development` | Environment |
| `JUPITER_API_KEY` | *(optional)* | Jupiter Trigger API key |

---

## HTTP Endpoints

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Service health |

### Actions (Authenticated)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/actions/quote` | Jupiter swap quote |
| `POST` | `/actions/cross-chain-quote` | Relay cross-chain quote |
| `POST` | `/actions/build` | Build transaction |
| `POST` | `/actions/simulate` | Simulate transaction |
| `POST` | `/actions/simulate-advanced` | Advanced simulation (WIP) |
| `GET` | `/actions/limit-orders` | List Jupiter limit orders |
| `GET` | `/actions/dca-orders` | List Jupiter DCA orders |
| `GET` | `/actions/chains` | Supported cross-chain chains |
| `GET` | `/actions/chains/{id}/tokens` | Chain tokens |
| `GET` | `/actions/{id}` | Get transaction by ID |

### Transactions (Authenticated)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/transactions` | List transactions |
| `POST` | `/transactions` | Create transaction record |
| `GET` | `/transactions/{id}` | Get transaction |
| `PATCH` | `/transactions/{id}/status` | Update status |

### Protocols (Authenticated)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/protocols` | List all protocols |
| `GET` | `/protocols/{id}/stats` | Protocol details |
| `GET` | `/protocols/tokens` | List common tokens |
| `GET` | `/protocols/tokens/{symbol}` | Token by symbol |

### Tokens (Alias)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tokens` | List common tokens |
| `GET` | `/tokens/{symbol}` | Token by symbol |

---

## Action Types

### `builder.rs` - Action Router

```rust
pub enum ActionType {
    // Transfers
    Transfer,
    Burn,

    // Swaps
    Swap,
    CrossChainSwap,

    // Staking
    Stake,
    Unstake,

    // Lending
    Lend,
    WithdrawLend,
    Borrow,
    Repay,

    // Perps
    PerpOpen,
    PerpClose,

    // Liquidity
    AddLiquidity,
    RemoveLiquidity,

    // Token Launch
    LaunchToken,

    // Orders
    LimitOrder,
    DcaOrder,

    // NFT
    NftBuy,
    NftSell,
    NftMint,
}
```

### Build Request

```json
{
  "actionType": "swap",
  "params": {
    "inputMint": "So11111111111111111111111111111111111111112",
    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "amount": "1000000000",
    "slippageBps": 50
  }
}
```

### Build Response

```json
{
  "transaction": "BASE64_ENCODED_TX",
  "lastValidBlockHeight": 123456789,
  "computeUnitLimit": 200000,
  "computeUnitPrice": 1000,
  "simulation": {
    "success": true,
    "unitsConsumed": 150000,
    "logs": ["..."]
  }
}
```

---

## Protocol Registry

### Supported Protocols

| ID | Name | Category | Actions |
|----|------|----------|---------|
| `jupiter` | Jupiter | DEX Aggregator | swap, limit_order, dca, lend, borrow, perp |
| `jupiter_lend` | Jupiter Lend | Lending | lend, withdraw_lend, borrow, repay |
| `jupiter_perp` | Jupiter Perps | Perpetuals | perp_open, perp_close, jlp_add, jlp_remove |
| `pump_fun` | Pump.fun | Token Launchpad | launch_token, buy, sell |
| `raydium` | Raydium | DEX / AMM | swap, provide_liquidity, remove_liquidity |
| `marinade` | Marinade Finance | Liquid Staking | stake, unstake, delayed_unstake |
| `jito` | Jito | Liquid Staking + MEV | stake, unstake, tip, bundle |
| `orca` | Orca | DEX / CLMM | swap, add_liquidity, open_position |
| `meteora` | Meteora | DEX / DLMM | swap, add_liquidity, claim_fees |
| `drift` | Drift Protocol | Perpetuals + DEX | perp_open, deposit, borrow, twap |
| `kamino` | Kamino Finance | Yield Optimizer | deposit, multiply_open, vault_deposit |

---

## Services

### Swap Service

```rust
// services/swap.rs
pub struct SwapParams {
    pub input_mint: String,
    pub output_mint: String,
    pub amount: String,
    pub slippage_bps: Option<u16>,
    pub only_direct_routes: Option<bool>,
}

pub async fn get_swap_quote(
    http: &reqwest::Client,
    params: &SwapParams,
) -> Result<serde_json::Value, AppError>

pub async fn build_swap_transaction(
    http: &reqwest::Client,
    quote: &serde_json::Value,
    user_pubkey: &Pubkey,
) -> Result<VersionedTransaction, AppError>
```

**Jupiter API:** `https://quote-api.jup.ag/v6`

### Transfer Service

```rust
// services/transfer.rs
pub struct TransferParams {
    pub to: String,
    pub mint: Option<String>,  // None = SOL
    pub amount: String,
    pub decimals: Option<u8>,
}

pub async fn build_transfer(
    rpc: &SolanaRpc,
    user: &Pubkey,
    params: &TransferParams,
) -> Result<BuildResult, AppError>
```

### Simulation Service

```rust
// services/simulation.rs
pub struct SimulationResult {
    pub success: bool,
    pub units_consumed: Option<u64>,
    pub logs: Vec<String>,
    pub balance_changes: Vec<BalanceChange>,
    pub error: Option<String>,
}
```

---

## Solana RPC Wrapper

### SolanaRpc

```rust
// solana/connection.rs
pub struct SolanaRpc {
    client: Arc<RpcClient>,
    endpoint: String,
    network: String,
    multi_rpc: Option<Arc<MultiRpc>>,
}

impl SolanaRpc {
    pub fn new(endpoint: &str, network: &str) -> Self;
    pub fn new_multi(primary: &str, fallbacks: Vec<String>, network: &str) -> Self;

    pub fn client(&self) -> &RpcClient;
    pub fn multi_rpc(&self) -> Option<&Arc<MultiRpc>>;
    pub fn is_mainnet(&self) -> bool;

    pub fn get_latest_blockhash_with_retry(&self) -> Result<Hash, ClientError>;
    pub fn health_check(&self) -> Result<u64, ClientError>;
}
```

### Multi-RPC

```rust
// solana/multi_rpc.rs
pub struct MultiRpc {
    primary: String,
    fallbacks: Vec<String>,
    timeout_secs: u64,
    max_retries: u32,
}

impl MultiRpc {
    pub async fn execute_with_fallback<F, T>(&self, op: F) -> Result<T, Error>;
    pub async fn get_health_status(&self) -> Vec<RpcHealth>;
}
```

---

## Database Model

### Transaction

```rust
// db/models.rs
pub struct Transaction {
    pub id: Uuid,
    pub user_id: Uuid,
    pub action: String,
    pub status: String,        // pending | submitted | confirmed | failed | cancelled
    pub tx_hash: Option<String>,
    pub protocol: Option<String>,
    pub chain: String,         // default: "solana"
    pub parameters: Value,     // JSON
    pub actual_fee: Option<String>,
    pub error_message: Option<String>,
    pub chat_session_id: Option<Uuid>,
    pub chat_message_id: Option<Uuid>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub submitted_at: Option<DateTime<Utc>>,
    pub confirmed_at: Option<DateTime<Utc>>,
}
```

---

## gRPC Service

### HealthService

```protobuf
// proto/common/health.proto
service HealthService {
  rpc Check(HealthCheckRequest) returns (HealthCheckResponse);
}
```

```rust
// grpc/health.rs
pub struct HealthServiceImpl {
    pub state: GrpcState,
    pub started_at: std::time::Instant,
}

impl HealthService for HealthServiceImpl {
    async fn check(&self, _: Request<HealthCheckRequest>) -> Result<Response<HealthCheckResponse>, Status>
}
```

**Note:** SolanaActionService, SolanaQuoteService, SolanaProtocolService are defined as Rust traits but not yet exposed via gRPC (pending proto definitions).

---

## Middleware

### InternalAuth

```rust
// middleware/auth.rs
pub struct InternalAuth {
    api_key: String,
}

impl<S> Transform<S, ServiceRequest> for InternalAuth
where
    S: Service<ServiceRequest, Response = ServiceResponse, Error = Error>,
{
    fn new_transform(&self, service: S) -> Self::Future {
        // Validates X-Internal-Api-Key header
        // Extracts X-User-Wallet and injects UserWallet extension
    }
}
```

---

## Error Handling

```rust
// error.rs
#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("Unauthorized: {0}")]
    Unauthorized(String),

    #[error("Invalid parameters: {0}")]
    InvalidParams(String),

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Database error: {0}")]
    DatabaseError(String),

    #[error("Solana RPC error: {0}")]
    SolanaRpcError(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl ResponseError for AppError {
    fn error_response(&self) -> HttpResponse {
        match self {
            AppError::Unauthorized(_) => HttpResponse::Unauthorized().json(...),
            AppError::InvalidParams(_) => HttpResponse::BadRequest().json(...),
            AppError::NotFound(_) => HttpResponse::NotFound().json(...),
            AppError::DatabaseError(_) => HttpResponse::InternalServerError().json(...),
            AppError::SolanaRpcError(_) => HttpResponse::BadGateway().json(...),
            AppError::Internal(_) => HttpResponse::InternalServerError().json(...),
        }
    }
}
```

---

## Dependencies

```toml
# Cargo.toml
[dependencies]
actix-web = "4"
tonic = "0.12"
prost = "0.13"

solana-sdk = "1.18"
solana-client = "1.18"
spl-token = "4"
spl-associated-token-account = "3"

tokio = { version = "1", features = ["full"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"

diesel = { version = "2", features = ["postgres", "uuid", "chrono", "serde_json"] }
diesel-async = { version = "0.5", features = ["postgres", "deadpool"] }

reqwest = { version = "0.12", features = ["json", "multipart"] }
uuid = { version = "1", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
thiserror = "2"
anyhow = "1"
tracing = "0.1"
tracing-actix-web = "0.7"
prometheus = "0.13"

[build-dependencies]
tonic-build = "0.12"
```

---

## Build & Run

```bash
# Development
cd services/solana-service-rs
cargo run

# Release build
cargo build --release

# Run tests
cargo test

# Check without building
cargo check
```

---

## Testing

```bash
# All tests
cargo test

# Specific test
cargo test test_swap_quote

# With output
cargo test -- --nocapture
```

---

## Prometheus Metrics

Available at `/metrics` (if configured):
- `solana_service_requests_total`
- `solana_service_request_duration_seconds`
- `solana_rpc_health`
- `solana_rpc_latency_seconds`
