use tonic::{Request, Response, Status};

// Include the generated protobuf code.
pub mod pb {
    tonic::include_proto!("oprai.common");
}

use pb::health_service_server::HealthService;
use pb::{HealthCheckRequest, HealthCheckResponse, ServiceStatus, ServingStatus};

use super::GrpcState;

/// gRPC HealthService implementation.
pub struct HealthServiceImpl {
    pub state: GrpcState,
    pub started_at: std::time::Instant,
}

#[tonic::async_trait]
impl HealthService for HealthServiceImpl {
    async fn check(
        &self,
        _request: Request<HealthCheckRequest>,
    ) -> Result<Response<HealthCheckResponse>, Status> {
        let uptime = self.started_at.elapsed().as_secs_f64();

        // Check Solana RPC.
        let solana_status = match self.state.rpc.health_check() {
            Ok(_slot) => ServiceStatus {
                name: "solana-rpc".into(),
                status: ServingStatus::Serving.into(),
                message: String::new(),
                latency_ms: 0.0,
            },
            Err(e) => ServiceStatus {
                name: "solana-rpc".into(),
                status: ServingStatus::NotServing.into(),
                message: e.to_string(),
                latency_ms: 0.0,
            },
        };

        // Check DB pool.
        let db_status = {
            let status = self.state.pool.status();
            ServiceStatus {
                name: "postgres".into(),
                status: ServingStatus::Serving.into(),
                message: format!("pool size={}, available={}", status.size, status.available),
                latency_ms: 0.0,
            }
        };

        let response = HealthCheckResponse {
            status: ServingStatus::Serving.into(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            uptime_seconds: uptime,
            started_at: None, // Would require prost_types::Timestamp
            services: vec![solana_status, db_status],
        };

        Ok(Response::new(response))
    }
}
