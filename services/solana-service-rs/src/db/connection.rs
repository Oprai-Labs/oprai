use diesel_async::pooled_connection::deadpool::Pool;
use diesel_async::pooled_connection::AsyncDieselConnectionManager;
use diesel_async::AsyncPgConnection;

/// Type alias for the async connection pool.
pub type DbPool = Pool<AsyncPgConnection>;

/// Create a Deadpool-backed async Diesel connection pool.
///
/// Pool is intentionally small (5) to avoid overwhelming the single
/// PostgreSQL instance shared by all services. At K8s scale with N
/// replicas, total connections = N × 5 × services. With max_connections=100
/// and 5 services × 5 conns = 25 per single replica, up to 4 replicas fit
/// comfortably before a pgBouncer proxy becomes necessary.
pub fn create_pool(database_url: &str) -> DbPool {
    let manager = AsyncDieselConnectionManager::<AsyncPgConnection>::new(database_url);
    Pool::builder(manager)
        .max_size(5)
        .build()
        .expect("Failed to create database connection pool")
}
