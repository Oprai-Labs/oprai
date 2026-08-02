use std::env;

/// Application configuration loaded from environment variables.
#[derive(Debug, Clone)]
pub struct Config {
    /// HTTP REST port (default 3030).
    pub port: u16,
    /// gRPC port (default 50053).
    pub grpc_port: u16,
    /// Bind address for HTTP server (default "127.0.0.1" — loopback only).
    /// Set to "0.0.0.0" only inside Docker/container environments where network
    /// isolation is provided by the container orchestrator.
    pub bind_host: String,
    /// Bind address for gRPC server (default "127.0.0.1").
    pub grpc_bind_host: String,
    /// PostgreSQL connection string.
    pub database_url: String,
    /// Solana JSON-RPC endpoints (comma-separated for multi-RPC).
    pub solana_rpc: String,
    /// Fallback RPC endpoints (comma-separated).
    pub solana_rpc_fallback: String,
    /// Solana network name (mainnet-beta | devnet | testnet).
    pub solana_network: String,
    /// Shared secret for gateway <-> service auth.
    pub internal_api_key: String,
    /// Runtime environment.
    pub node_env: String,
    /// Jupiter API key (required for Trigger/Recurring APIs).
    pub jupiter_api_key: Option<String>,
    /// Helius API key (Enhanced Transactions, DAS, Priority Fee, Wallet API).
    pub helius_api_key: Option<String>,
    /// URL of the TypeScript solana-service (handles Streamflow SDK operations).
    /// Defaults to http://localhost:3031 — set STREAMFLOW_SDK_URL in env.
    pub streamflow_sdk_url: String,
    /// Wallet address to receive Relay.link app fees (EVM address or Solana pubkey).
    /// When set, a 0.05% fee is automatically appended to every relay_bridge quote.
    /// Leave unset in development to disable fee collection.
    pub relay_fee_recipient: Option<String>,
    /// Relay.link API key for operator-level endpoints (e.g. fast-fill).
    /// Obtain from the Relay team. Leave unset to disable fast-fill.
    pub relay_api_key: Option<String>,
    /// HTTP base URL of auth-service (used for /internal/spending/check
    /// and /internal/spending/commit). Defaults to the dev endpoint.
    pub auth_service_url: String,
}

impl Config {
    /// Build config from environment, panicking on missing required values.
    pub fn from_env() -> Self {
        Self {
            port: env::var("PORT")
                .unwrap_or_else(|_| "3030".into())
                .parse()
                .expect("PORT must be a valid u16"),
            grpc_port: env::var("GRPC_PORT")
                .unwrap_or_else(|_| "50053".into())
                .parse()
                .expect("GRPC_PORT must be a valid u16"),
            // Default to 127.0.0.1 (loopback) so the service is only reachable
            // via the gateway on the same host. Set BIND_HOST=0.0.0.0 in
            // Docker/Compose environments where network isolation is handled
            // at the container/orchestration layer.
            bind_host: env::var("BIND_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
            grpc_bind_host: env::var("GRPC_BIND_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
            database_url: env::var("DATABASE_URL").unwrap_or_else(|_| {
                let host = env::var("DB_HOST").unwrap_or_else(|_| "localhost".into());
                let port = env::var("DB_PORT").unwrap_or_else(|_| "5433".into());
                let name = env::var("DB_NAME").unwrap_or_else(|_| "oprai".into());
                let user = env::var("DB_USER").unwrap_or_else(|_| "postgres".into());
                let pass = env::var("DB_PASSWORD")
                    .or_else(|_| env::var("DB_SUPERPASS"))
                    .unwrap_or_default();
                format!("postgres://{user}:{pass}@{host}:{port}/{name}")
            }),
            solana_rpc: env::var("SOLANA_RPC")
                .or_else(|_| env::var("NEXT_PUBLIC_SOLANA_RPC"))
                .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".into()),
            solana_rpc_fallback: env::var("SOLANA_RPC_FALLBACK")
                .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".into()),
            solana_network: env::var("SOLANA_NETWORK").unwrap_or_else(|_| "mainnet-beta".into()),
            internal_api_key: env::var("OPRAI_INTERNAL_API_KEY")
                .expect("OPRAI_INTERNAL_API_KEY must be set"),
            node_env: env::var("NODE_ENV").unwrap_or_else(|_| "development".into()),
            jupiter_api_key: env::var("JUPITER_API_KEY").ok().filter(|s| !s.is_empty()),
            helius_api_key: env::var("HELIUS_API_KEY").ok().filter(|s| !s.is_empty()),
            streamflow_sdk_url: env::var("STREAMFLOW_SDK_URL")
                .unwrap_or_else(|_| "http://localhost:3031".into()),
            // Relay's app fee goes to the same place as everything else unless
            // deliberately overridden. Keeping a second env var that has to be
            // set separately is how a configured commission ends up collected
            // on some paths and not others.
            relay_fee_recipient: env::var("RELAY_FEE_RECIPIENT")
                .ok()
                .filter(|s| !s.is_empty())
                .or_else(|| env::var("OPRAI_FEE_WALLET").ok().filter(|s| !s.is_empty())),
            relay_api_key: env::var("RELAY_API_KEY").ok().filter(|s| !s.is_empty()),
            auth_service_url: env::var("AUTH_SERVICE_HTTP")
                .unwrap_or_else(|_| "http://localhost:3010".into()),
        }
    }

    pub fn is_production(&self) -> bool {
        self.node_env == "production"
    }

    /// Validate critical config values for production safety.
    pub fn validate(&self) {
        const INSECURE_KEY: &str = "dev-internal-key-change";

        if self.internal_api_key == INSECURE_KEY {
            panic!(
                "FATAL: OPRAI_INTERNAL_API_KEY is set to the insecure default value. \
                 Set OPRAI_INTERNAL_API_KEY to a secure random value in all environments."
            );
        }
    }
}
