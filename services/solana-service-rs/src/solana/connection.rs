use solana_client::rpc_client::RpcClient;
use solana_sdk::commitment_config::CommitmentConfig;
use std::sync::Arc;
use std::time::Duration;

/// Wrapper around `RpcClient` with optional multi-endpoint support.
#[derive(Clone)]
pub struct SolanaRpc {
    client: Arc<RpcClient>,
    endpoint: String,
}

impl SolanaRpc {
    /// Create a new Solana RPC client wrapper (single endpoint).
    pub fn new(endpoint: &str) -> Self {
        let client = RpcClient::new_with_timeout_and_commitment(
            endpoint.to_string(),
            Duration::from_secs(60),
            CommitmentConfig::confirmed(),
        );
        Self {
            client: Arc::new(client),
            endpoint: endpoint.to_string(),
        }
    }

    /// Create a new Solana RPC client with fallback endpoint list.
    /// Currently uses the primary endpoint; fallbacks are reserved for future use.
    pub fn new_multi(primary_endpoint: &str, _fallback_endpoints: Vec<String>) -> Self {
        Self::new(primary_endpoint)
    }

    /// Access the inner `RpcClient`.
    pub fn client(&self) -> &RpcClient {
        &self.client
    }

    /// Get the configured RPC endpoint URL.
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    /// Fetch the latest blockhash with retry (up to 3 attempts).
    pub fn get_latest_blockhash_with_retry(
        &self,
    ) -> Result<solana_sdk::hash::Hash, solana_client::client_error::ClientError> {
        let mut last_err = None;
        for attempt in 0..3u32 {
            match self.client.get_latest_blockhash() {
                Ok(hash) => return Ok(hash),
                Err(e) => {
                    tracing::warn!(
                        attempt,
                        error = %e,
                        "get_latest_blockhash failed, retrying"
                    );
                    last_err = Some(e);
                    std::thread::sleep(Duration::from_millis(500 * u64::from(attempt + 1)));
                }
            }
        }
        Err(last_err.unwrap())
    }

    /// Health-check: try to fetch the latest slot.
    pub fn health_check(&self) -> Result<u64, solana_client::client_error::ClientError> {
        self.client.get_slot()
    }
}
