use chrono::{DateTime, Utc};
use diesel::prelude::*;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::db::schema::{transaction_events, transactions};

// ──────────────────────────────────────────────────────────────────────────────
// transaction_events — immutable append-only audit log
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Queryable, Selectable, Serialize, Deserialize)]
#[diesel(table_name = transaction_events)]
#[diesel(check_for_backend(diesel::pg::Pg))]
pub struct TransactionEvent {
    pub id: Uuid,
    pub transaction_id: Uuid,
    pub event_type: String,
    pub from_status: Option<String>,
    pub to_status: String,
    pub tx_hash: Option<String>,
    pub slot: Option<i64>,
    pub block_height: Option<i64>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub error_category: Option<String>,
    pub metadata: serde_json::Value,
    pub duration_ms: Option<i32>,
    pub ip_address: Option<String>,
    pub user_agent: Option<String>,
    pub request_id: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Insertable)]
#[diesel(table_name = transaction_events)]
pub struct NewTransactionEvent {
    pub transaction_id: Uuid,
    pub event_type: String,
    pub from_status: Option<String>,
    pub to_status: String,
    pub tx_hash: Option<String>,
    pub slot: Option<i64>,
    pub block_height: Option<i64>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub error_category: Option<String>,
    pub metadata: serde_json::Value,
    pub duration_ms: Option<i32>,
    pub ip_address: Option<String>,
    pub user_agent: Option<String>,
    pub request_id: Option<String>,
}

impl NewTransactionEvent {
    pub fn status_change(
        transaction_id: Uuid,
        event_type: &str,
        from_status: Option<&str>,
        to_status: &str,
    ) -> Self {
        Self {
            transaction_id,
            event_type: event_type.to_string(),
            from_status: from_status.map(|s| s.to_string()),
            to_status: to_status.to_string(),
            tx_hash: None,
            slot: None,
            block_height: None,
            error_code: None,
            error_message: None,
            error_category: None,
            metadata: serde_json::json!({}),
            duration_ms: None,
            ip_address: None,
            user_agent: None,
            request_id: None,
        }
    }
}

// ──────────────────────────────────────────────────────────────────────────────
// Queryable model (SELECT)
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Queryable, Selectable, Serialize, Deserialize)]
#[diesel(table_name = transactions)]
#[diesel(check_for_backend(diesel::pg::Pg))]
pub struct Transaction {
    pub id: Uuid,
    pub user_id: Uuid,
    pub user_wallet: String,
    pub chat_session_id: Option<Uuid>,
    pub chat_message_id: Option<Uuid>,
    pub tx_hash: Option<String>,
    pub chain: String,
    pub status: String,
    pub action: String,
    pub protocol: Option<String>,
    pub parameters: serde_json::Value,
    pub estimated_fee: Option<String>,
    pub actual_fee: Option<String>,
    pub error_message: Option<String>,
    pub initiated_at: Option<DateTime<Utc>>,
    pub submitted_at: Option<DateTime<Utc>>,
    pub confirmed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

// ──────────────────────────────────────────────────────────────────────────────
// Insertable model (INSERT)
// ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Insertable, Serialize, Deserialize)]
#[diesel(table_name = transactions)]
pub struct NewTransaction {
    pub id: Uuid,
    pub user_id: Uuid,
    pub user_wallet: String,
    pub chat_session_id: Option<Uuid>,
    pub chat_message_id: Option<Uuid>,
    pub tx_hash: Option<String>,
    pub chain: String,
    pub status: String,
    pub action: String,
    pub protocol: Option<String>,
    pub parameters: serde_json::Value,
    pub estimated_fee: Option<String>,
    pub actual_fee: Option<String>,
    pub error_message: Option<String>,
    pub initiated_at: Option<DateTime<Utc>>,
    pub submitted_at: Option<DateTime<Utc>>,
    pub confirmed_at: Option<DateTime<Utc>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl NewTransaction {
    /// Create a new pending transaction record.
    pub fn new(
        user_id: Uuid,
        user_wallet: String,
        action: &str,
        parameters: serde_json::Value,
    ) -> Self {
        let now = Utc::now();
        Self {
            id: Uuid::new_v4(),
            user_id,
            user_wallet,
            chat_session_id: None,
            chat_message_id: None,
            tx_hash: None,
            chain: "solana".to_string(),
            status: "pending".to_string(),
            action: action.to_string(),
            protocol: None,
            parameters,
            estimated_fee: None,
            actual_fee: None,
            error_message: None,
            initiated_at: Some(now),
            submitted_at: None,
            confirmed_at: None,
            created_at: now,
            updated_at: now,
        }
    }
}

