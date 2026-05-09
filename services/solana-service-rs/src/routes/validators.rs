use actix_web::{get, web, HttpResponse};

use crate::error::AppError;
use crate::routes::actions::AppState;
use crate::services::validators;

/// GET /validators/top
///
/// Returns the top 20 Solana validators sorted by effective stake
/// (activatedStakeSol × (1 − commission/100)), filtered to commission ≤ 10%.
/// Designed for the action-card validator picker — the call is blocking so it
/// runs inside web::block to avoid starving the async runtime.
#[get("/top")]
pub async fn get_top_validators(state: web::Data<AppState>) -> Result<HttpResponse, AppError> {
    let rpc = state.rpc.clone();

    let result = web::block(move || validators::get_top_validators(rpc.client()))
        .await
        .map_err(|e| AppError::Internal(format!("thread pool error: {e}")))?;

    let vals = result?;

    Ok(HttpResponse::Ok().json(serde_json::json!({
        "validators": vals,
        "count": vals.len(),
        "note": "Sorted by effective stake. APY is an estimate based on ~7% network inflation; actual rewards vary."
    })))
}
