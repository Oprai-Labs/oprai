package db

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// NewPool creates a pgx connection pool from the given DATABASE_URL.
// It verifies connectivity with a Ping and ensures the target schema exists.
//
// pgBouncer (transaction mode) compatibility:
//   - QueryExecModeSimpleProtocol: disables prepared statements.
//     pgBouncer transaction mode reassigns backend connections between requests,
//     so a prepared statement created on connection A is invisible on connection B.
//     Simple protocol sends SQL text every time — no prepared statement cache needed.
//   - Pool size: kept small because pgBouncer multiplexes hundreds of app connections
//     into a small backend pool.  Each service replica holds 3 backend connections;
//     at 5 services × N replicas × 3 = total backend connections to pgBouncer.
func NewPool(ctx context.Context, databaseURL, schema string) (*pgxpool.Pool, error) {
	if databaseURL == "" {
		return nil, fmt.Errorf("DATABASE_URL is empty")
	}

	config, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, fmt.Errorf("parsing DATABASE_URL: %w", err)
	}

	// Disable prepared statements — required for pgBouncer transaction mode.
	config.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol

	// Connection pool tuning (small because pgBouncer handles multiplexing).
	config.MaxConns = 3
	config.MinConns = 1
	config.MaxConnLifetime = 30 * time.Minute
	config.MaxConnIdleTime = 5 * time.Minute
	config.HealthCheckPeriod = 30 * time.Second

	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("creating pgx pool: %w", err)
	}

	// Verify connectivity
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("pinging database: %w", err)
	}

	// Ensure the schema exists
	if schema != "" {
		createSchemaSQL := fmt.Sprintf(`CREATE SCHEMA IF NOT EXISTS %q`, schema)
		if _, err := pool.Exec(ctx, createSchemaSQL); err != nil {
			pool.Close()
			return nil, fmt.Errorf("creating schema %q: %w", schema, err)
		}
	}

	log.Printf("[auth-service-go] Connected to PostgreSQL (pool: min=%d, max=%d)", config.MinConns, config.MaxConns)
	return pool, nil
}
