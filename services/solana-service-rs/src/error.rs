use actix_web::{HttpResponse, ResponseError};
use serde::Serialize;
use std::fmt;

/// Strip secret query-parameter values (api keys, tokens) from an error string.
///
/// reqwest and the Solana RPC client embed the *full request URL* in their
/// error Display — and our RPC endpoints carry the Helius key in the query
/// string (`https://mainnet.helius-rpc.com/?api-key=<secret>`). That URL then
/// rode into both the `error = %self` log line and the tracing_actix_web
/// `exception.message` Debug line on every upstream 5xx, leaking the key into
/// the logs. Redact the value of any `api-key` / `api_key` / `access_token` /
/// `key` query param, keeping the param name so the message still reads.
pub fn redact_secrets(input: &str) -> String {
    const SECRET_PARAMS: [&str; 4] = ["api-key=", "api_key=", "access_token=", "key="];
    let mut out = input.to_string();
    for param in SECRET_PARAMS {
        // Scan forward with a cursor so we never re-match a token we just
        // rewrote (the param *name* is kept, so find-from-0 would loop forever).
        let mut cursor = 0;
        while let Some(rel) = out[cursor..].find(param) {
            let val_start = cursor + rel + param.len();
            // Value runs until the next delimiter that ends a query-string token.
            let val_len = out[val_start..]
                .find(|c: char| matches!(c, '&' | '"' | ')' | ' ' | '\'' | '#' | ',' | '\n'))
                .unwrap_or(out.len() - val_start);
            if val_len > 0 {
                out.replace_range(val_start..val_start + val_len, "REDACTED");
            }
            cursor = val_start + "REDACTED".len().min(out.len() - val_start);
            if cursor >= out.len() {
                break;
            }
        }
    }
    out
}

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

impl AppError {
    /// The part of the error a user is meant to read.
    ///
    /// `Display` prefixes the variant name — "Relay API error: that amount is
    /// too small to bridge". The tail is written for the user; the head is a
    /// fact about our plumbing, and putting the two together tells someone
    /// whose amount was too small that an API broke. It reads as our fault and
    /// gives them nothing to act on, when the sentence right behind it told
    /// them exactly what to do.
    ///
    /// So the label stays where it is useful — the log line below, where
    /// knowing *which* upstream refused is the whole point — and never reaches
    /// the response body.
    pub fn user_message(&self) -> &str {
        match self {
            AppError::DatabaseError(m)
            | AppError::SolanaRpcError(m)
            | AppError::JupiterApiError(m)
            | AppError::RelayApiError(m)
            | AppError::InvalidParams(m)
            | AppError::Unauthorized(m)
            | AppError::NotFound(m)
            | AppError::ProtocolError(m)
            | AppError::Internal(m) => m,
        }
    }
}

/// JSON body returned for error responses.
#[derive(Serialize)]
struct ErrorResponse {
    error: String,
}

impl ResponseError for AppError {
    fn error_response(&self) -> HttpResponse {
        tracing::warn!(error = %redact_secrets(&self.to_string()), "request failed");
        let body = ErrorResponse {
            error: redact_secrets(self.user_message()),
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
        // Solana RPC client errors embed the full endpoint URL (Helius key in
        // the query string) — redact before it ever reaches a log or response.
        AppError::SolanaRpcError(redact_secrets(&e.to_string()))
    }
}

impl From<reqwest::Error> for AppError {
    fn from(e: reqwest::Error) -> Self {
        // Default to Jupiter API error for backward compatibility
        // Relay-specific code should use .map_err(|e| AppError::RelayApiError(e.to_string()))
        // reqwest's Display includes the request URL — redact query-string secrets.
        AppError::JupiterApiError(redact_secrets(&e.to_string()))
    }
}

impl From<solana_sdk::pubkey::ParsePubkeyError> for AppError {
    fn from(e: solana_sdk::pubkey::ParsePubkeyError) -> Self {
        AppError::InvalidParams(format!("Invalid public key: {e}"))
    }
}

#[cfg(test)]
mod tests {
    use super::redact_secrets;

    #[test]
    fn redacts_helius_key_in_rpc_url() {
        let leaked = "fetch_positions_for_owner: HTTP status server error (502 Bad Gateway) \
                      for url (https://mainnet.helius-rpc.com/?api-key=1f7b9edf-17ec-442e-ad8d-32b1e78b0caf)";
        let out = redact_secrets(leaked);
        assert!(!out.contains("1f7b9edf"), "key leaked: {out}");
        assert!(out.contains("api-key=REDACTED"), "param name should remain: {out}");
        assert!(out.contains("for url (https://mainnet.helius-rpc.com/?"));
    }

    #[test]
    fn redacts_multiple_and_variants() {
        let s = "a?api_key=SECRET1&x=1 b?access_token=SECRET2&y https://h/?key=SECRET3";
        let out = redact_secrets(s);
        assert!(!out.contains("SECRET1") && !out.contains("SECRET2") && !out.contains("SECRET3"), "{out}");
        assert!(out.contains("api_key=REDACTED"));
        assert!(out.contains("access_token=REDACTED"));
        assert!(out.contains("key=REDACTED"));
        assert!(out.contains("&x=1"), "non-secret params preserved: {out}");
    }

    #[test]
    fn leaves_clean_strings_untouched() {
        let s = "Invalid parameters: Invalid wallet address";
        assert_eq!(redact_secrets(s), s);
    }

    #[test]
    fn handles_trailing_key_at_end_of_string() {
        let s = "url=https://h/?api-key=ABCDEF";
        let out = redact_secrets(s);
        assert_eq!(out, "url=https://h/?api-key=REDACTED");
    }
}
