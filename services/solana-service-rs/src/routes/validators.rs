use actix_web::{get, web, HttpResponse};

use crate::error::AppError;
use crate::routes::actions::AppState;
use crate::services::validators;

/// GET /validators/top
///
/// Validators a user can choose between, named where they can be named:
/// Stakewiz for identity + measured APY, the Solana RPC as an anonymous
/// fallback. Sorted by stake, commission ≤ 10%, delinquents excluded.
#[get("/top")]
pub async fn get_top_validators(state: web::Data<AppState>) -> Result<HttpResponse, AppError> {
    // The RPC call inside the fallback is blocking, so it is fenced off in
    // `spawn_blocking` there rather than here — this handler is otherwise a
    // plain HTTP fetch and must not occupy a blocking thread waiting on it.
    let rpc = state.rpc.clone();
    let http = state.http.clone();
    let vals = tokio::task::spawn_blocking(move || {
        tokio::runtime::Handle::current()
            .block_on(validators::get_top_validators(rpc.client(), &http))
    })
    .await
    .map_err(|e| AppError::Internal(format!("thread pool error: {e}")))??;

    Ok(HttpResponse::Ok().json(serde_json::json!({
        "validators": vals,
        "count": vals.len(),
    })))
}
