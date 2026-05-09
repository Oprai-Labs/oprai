package db

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Connect creates a new PostgreSQL connection pool.
//
// pgBouncer (transaction mode) compatibility:
//   - QueryExecModeSimpleProtocol: disables prepared statements so pgBouncer
//     can reassign backend connections freely between requests.
func Connect(ctx context.Context, databaseURL string) (*pgxpool.Pool, error) {
	config, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, fmt.Errorf("failed to parse database URL: %w", err)
	}

	// Disable prepared statements — required for pgBouncer transaction mode.
	config.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol

	// Admin service is low-traffic; 2 backend connections is ample.
	// pgBouncer multiplexes app connections so a small pool is correct.
	config.MaxConns = 2
	config.MinConns = 1
	config.MaxConnLifetime = 30 * time.Minute
	config.MaxConnIdleTime = 5 * time.Minute
	config.HealthCheckPeriod = 30 * time.Second

	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("failed to create connection pool: %w", err)
	}

	// Verify connectivity
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("failed to ping database: %w", err)
	}

	slog.Info("database connected", "max_conns", config.MaxConns)
	return pool, nil
}
