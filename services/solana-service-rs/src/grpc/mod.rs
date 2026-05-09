pub mod health;

use std::sync::Arc;

use crate::db::connection::DbPool;
use crate::solana::connection::SolanaRpc;

/// Shared state for gRPC service implementations.
#[derive(Clone)]
pub struct GrpcState {
    pub pool: DbPool,
    pub rpc: Arc<SolanaRpc>,
    #[allow(dead_code)]
    pub http: reqwest::Client,
    #[allow(dead_code)]
    pub jupiter_api_key: Option<String>,
    #[allow(dead_code)]
    pub helius_api_key: Option<String>,
    #[allow(dead_code)]
    pub relay_fee_recipient: Option<String>,
    #[allow(dead_code)]
    pub relay_api_key: Option<String>,
}
