use actix_web::{
    body::EitherBody,
    dev::{forward_ready, Service, ServiceRequest, ServiceResponse, Transform},
    Error, HttpMessage, HttpResponse,
};
use std::future::{self, Future, Ready};
use std::pin::Pin;
use std::rc::Rc;
use subtle::ConstantTimeEq;

/// Key used to store the authenticated wallet address in request extensions.
#[derive(Debug, Clone)]
pub struct UserWallet(pub String);

/// Actix middleware that validates `X-Internal-Api-Key` and extracts `X-User-Wallet`.
///
/// This mirrors the Node.js `@oprai/auth-middleware` behaviour: the gateway
/// validates the JWT and injects both headers before proxying to this service.
pub struct InternalAuth {
    api_key: String,
}

impl InternalAuth {
    pub fn new(api_key: String) -> Self {
        Self { api_key }
    }
}

impl<S, B> Transform<S, ServiceRequest> for InternalAuth
where
    S: Service<ServiceRequest, Response = ServiceResponse<B>, Error = Error> + 'static,
    B: 'static,
{
    type Response = ServiceResponse<EitherBody<B>>;
    type Error = Error;
    type Transform = InternalAuthMiddleware<S>;
    type InitError = ();
    type Future = Ready<Result<Self::Transform, Self::InitError>>;

    fn new_transform(&self, service: S) -> Self::Future {
        future::ready(Ok(InternalAuthMiddleware {
            service: Rc::new(service),
            api_key: self.api_key.clone(),
        }))
    }
}

pub struct InternalAuthMiddleware<S> {
    service: Rc<S>,
    api_key: String,
}

impl<S, B> Service<ServiceRequest> for InternalAuthMiddleware<S>
where
    S: Service<ServiceRequest, Response = ServiceResponse<B>, Error = Error> + 'static,
    B: 'static,
{
    type Response = ServiceResponse<EitherBody<B>>;
    type Error = Error;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>>>>;

    forward_ready!(service);

    fn call(&self, req: ServiceRequest) -> Self::Future {
        // Extract headers before moving into async block.
        let api_key_header = req
            .headers()
            .get("x-internal-api-key")
            .and_then(|v| v.to_str().ok())
            .map(String::from);

        let wallet_header = req
            .headers()
            .get("x-user-wallet")
            .and_then(|v| v.to_str().ok())
            .map(String::from);

        let expected_key = self.api_key.clone();
        let svc = self.service.clone();

        Box::pin(async move {
            // Validate the internal API key using constant-time comparison
            // to prevent timing-based side-channel attacks.
            let key_valid = api_key_header
                .as_deref()
                .map(|k| k.as_bytes().ct_eq(expected_key.as_bytes()).into())
                .unwrap_or(false);
            if !key_valid {
                tracing::warn!(
                    reason = "invalid_or_missing_api_key",
                    "Internal auth rejected: invalid or missing API key"
                );
                let response = HttpResponse::Unauthorized()
                    .json(serde_json::json!({ "error": "Unauthorized" }));
                return Ok(req.into_response(response).map_into_right_body());
            }

            // Extract user wallet (required for authenticated routes).
            match wallet_header {
                Some(wallet) if !wallet.is_empty() => {
                    req.extensions_mut().insert(UserWallet(wallet));
                }
                _ => {
                    tracing::warn!(
                        reason = "missing_wallet_header",
                        "Internal auth rejected: missing wallet header"
                    );
                    let response = HttpResponse::Unauthorized()
                        .json(serde_json::json!({ "error": "Unauthorized: missing wallet" }));
                    return Ok(req.into_response(response).map_into_right_body());
                }
            }

            svc.call(req).await.map(|res| res.map_into_left_body())
        })
    }
}
