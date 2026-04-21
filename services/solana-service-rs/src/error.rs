use actix_web::{HttpResponse, ResponseError};
use serde::Serialize;
use std::fmt;

/// Unified error type for the solana-service.
#[derive(Debug, thiserror::Error)]
pub enum AppError {
    #[error("Database error: {0}")]
    DatabaseError(String),

    #[error("Solana RPC error: {0}")]
    SolanaRpcError(String),

    #[error("Jupiter API error: {0}")]
    JupiterApiError(String),

    #[error("Relay API error: {0}")]
    RelayApiError(String),

    #[error("Invalid parameters: {0}")]
    InvalidParams(String),

    #[error("Unauthorized: {0}")]
    Unauthorized(String),

    #[error("Not found: {0}")]
    NotFound(String),

    #[error("Protocol error: {0}")]
    ProtocolError(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

/// JSON body returned for error responses.
#[derive(Serialize)]
struct ErrorResponse {
    error: String,
}

impl ResponseError for AppError {
    fn error_response(&self) -> HttpResponse {
        let body = ErrorResponse {
            error: self.to_string(),
        };

        match self {
            AppError::Unauthorized(_) => HttpResponse::Unauthorized().json(body),
            AppError::NotFound(_) => HttpResponse::NotFound().json(body),
            AppError::InvalidParams(_) => HttpResponse::BadRequest().json(body),
            AppError::DatabaseError(_)
            | AppError::SolanaRpcError(_)
            | AppError::JupiterApiError(_)
            | AppError::RelayApiError(_)
            | AppError::ProtocolError(_)
            | AppError::Internal(_) => HttpResponse::InternalServerError().json(body),
        }
    }
}

impl fmt::Display for ErrorResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.error)
    }
}

// ---- Conversion helpers ----

impl From<diesel::result::Error> for AppError {
    fn from(e: diesel::result::Error) -> Self {
        AppError::DatabaseError(e.to_string())
    }
}

impl From<solana_client::client_error::ClientError> for AppError {
    fn from(e: solana_client::client_error::ClientError) -> Self {
        AppError::SolanaRpcError(e.to_string())
    }
}

impl From<reqwest::Error> for AppError {
    fn from(e: reqwest::Error) -> Self {
        // Default to Jupiter API error for backward compatibility
        // Relay-specific code should use .map_err(|e| AppError::RelayApiError(e.to_string()))
        AppError::JupiterApiError(e.to_string())
    }
}

impl From<solana_sdk::pubkey::ParsePubkeyError> for AppError {
    fn from(e: solana_sdk::pubkey::ParsePubkeyError) -> Self {
        AppError::InvalidParams(format!("Invalid public key: {e}"))
    }
}
