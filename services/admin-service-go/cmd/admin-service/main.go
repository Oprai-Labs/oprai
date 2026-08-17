package main

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/joho/godotenv"
	"golang.org/x/crypto/bcrypt"

	adminassets "github.com/oprai/oprai/services/admin-service-go"
	"github.com/oprai/oprai/services/admin-service-go/internal/config"
	"github.com/oprai/oprai/services/admin-service-go/internal/db"
	"github.com/oprai/oprai/services/admin-service-go/internal/jobs"
	"github.com/oprai/oprai/services/admin-service-go/internal/middleware"
	"github.com/oprai/oprai/services/admin-service-go/internal/server"
)

func main() {
	// Load .env from repo root (best-effort, not fatal if missing)
	_ = godotenv.Load("../../.env")

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	cfg := config.Load()

	// Validate admin JWT secret
	const insecureDefault = "dev-admin-secret-change"
	isProd := cfg.Environment == "production"
	if cfg.AdminJWTSecret == insecureDefault {
		if isProd {
			slog.Error("OPRAI_ADMIN_JWT_SECRET is set to the insecure default, refusing to start in production")
			os.Exit(1)
		}
		slog.Warn("Using insecure default admin JWT secret, set OPRAI_ADMIN_JWT_SECRET before going to production")
	} else if len(cfg.AdminJWTSecret) < 32 {
		if isProd {
			slog.Error("OPRAI_ADMIN_JWT_SECRET must be at least 32 characters, refusing to start in production")
			os.Exit(1)
		}
		slog.Warn("OPRAI_ADMIN_JWT_SECRET is shorter than 32 characters, use a longer secret in production")
	}

	// Initialize database
	pool, err := db.Connect(context.Background(), cfg.DatabaseURL)
	if err != nil {
		slog.Error("Database connection failed", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	// Ensure admin_schema exists
	if _, err := pool.Exec(context.Background(), `CREATE SCHEMA IF NOT EXISTS "admin_schema"`); err != nil {
		slog.Error("Failed to create admin_schema", "error", err)
		os.Exit(1)
	}

	// (Re)apply the FAZ-2 analytics views in the background. Idempotent
	// (CREATE OR REPLACE), so this is safe on every boot; retried because the
	// views read tables owned by other services that may still be migrating on
	// a fresh stack. Never fatal — a stale view must not stop the admin API.
	go applyAnalyticsViews(pool)

	queries := db.NewQueries(pool)

	// Boot-time guard: refuse to run in production if the seeded admin
	// password is still the well-known default (`admin123`) or any other
	// trivially-weak password the original deployer left behind. The seed
	// script enforces a 16-char minimum on first install, but a manual SQL
	// edit could have inserted something weaker. We re-check on every start.
	if isProd {
		if err := assertNoDefaultAdminPassword(context.Background(), pool); err != nil {
			slog.Error("admin credential safety check failed", "error", err)
			os.Exit(1)
		}
	}

	// Start the audit log background worker.
	// Uses a cancellable context so it drains remaining jobs on shutdown.
	auditCtx, auditCancel := context.WithCancel(context.Background())
	defer auditCancel()
	middleware.StartAuditWorker(auditCtx)

	// Start daily stats background job.
	statsRunner := jobs.NewRunner(queries)
	statsRunner.Start(auditCtx) // shares the same cancellable context
	slog.Info("Daily stats job started")

	// Start HTTP server
	httpRouter := server.NewHTTPServer(cfg, queries)
	httpServer := &http.Server{
		Addr:         fmt.Sprintf(":%d", cfg.Port),
		Handler:      httpRouter,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Start gRPC server
	grpcServer := server.NewGRPCServer(cfg, queries)
	grpcListener, err := net.Listen("tcp", fmt.Sprintf(":%d", cfg.GRPCPort))
	if err != nil {
		slog.Error("failed to listen on gRPC port", "port", cfg.GRPCPort, "error", err)
		os.Exit(1)
	}

	// Channel to listen for errors from starting servers
	errCh := make(chan error, 2)

	go func() {
		slog.Info("Admin-service HTTP server starting", "port", cfg.Port, "cors_origin", cfg.AdminCORSOrigin)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- fmt.Errorf("HTTP server error: %w", err)
		}
	}()

	go func() {
		slog.Info("Admin-service gRPC server starting", "port", cfg.GRPCPort)
		if err := grpcServer.Serve(grpcListener); err != nil {
			errCh <- fmt.Errorf("gRPC server error: %w", err)
		}
	}()

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	select {
	case sig := <-quit:
		slog.Info("received shutdown signal", "signal", sig)
	case err := <-errCh:
		slog.Error("server error", "error", err)
	}

	slog.Info("Initiating graceful shutdown")

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	grpcServer.GracefulStop()

	if err := httpServer.Shutdown(ctx); err != nil {
		slog.Error("HTTP server shutdown error", "error", err)
	}

	slog.Info("Admin-service stopped")
}

// assertNoDefaultAdminPassword scans every admin user and refuses to start if
// any of them still holds a hash for one of the obvious default passwords.
// bcrypt is per-row (each hash uses a fresh salt) so we have to test each
// candidate password against each row — there's no fast equality check.
//
// Cost: 1 bcrypt comparison per (user × candidate). For the dozen-or-so admin
// rows a healthy install carries this is negligible, even at bcrypt cost 12.
// applyAnalyticsViews (re)creates the FAZ-2 analytics views. It retries because
// the views read tables owned by other services (solana/chat/analytics schemas)
// that may still be migrating on a fresh stack; on an established DB the first
// attempt succeeds. Never fatal.
func applyAnalyticsViews(pool *pgxpool.Pool) {
	const attempts = 12
	for i := 1; i <= attempts; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		_, err := pool.Exec(ctx, adminassets.AnalyticsViewsSQL)
		cancel()
		if err == nil {
			slog.Info("analytics views applied")
			return
		}
		slog.Warn("analytics views not applied yet (dependencies may still be migrating); will retry",
			"attempt", i, "maxAttempts", attempts, "error", err)
		time.Sleep(15 * time.Second)
	}
	slog.Error("analytics views could not be applied after retries; run sql/analytics_views.sql manually")
}

func assertNoDefaultAdminPassword(ctx context.Context, pool *pgxpool.Pool) error {
	candidates := []string{
		"admin123",
		"admin",
		"password",
		"changeme",
		"changeme123",
		"opraiadmin",
		"oprai123",
	}

	rows, err := pool.Query(ctx, `SELECT username, password_hash FROM admin_schema.admin_users`)
	if err != nil {
		return fmt.Errorf("querying admin_users: %w", err)
	}
	defer rows.Close()

	type adminRow struct {
		username string
		hash     string
	}
	var users []adminRow
	for rows.Next() {
		var r adminRow
		if err := rows.Scan(&r.username, &r.hash); err != nil {
			return fmt.Errorf("scanning admin row: %w", err)
		}
		users = append(users, r)
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterating admin rows: %w", err)
	}
	if len(users) == 0 {
		// No admin user yet — production must have been seeded before
		// the service starts. Refuse to come up empty.
		return fmt.Errorf("no admin users present; run scripts/db/seed_admin.sh before starting in production")
	}

	for _, u := range users {
		for _, candidate := range candidates {
			if err := bcrypt.CompareHashAndPassword([]byte(u.hash), []byte(candidate)); err == nil {
				return fmt.Errorf(
					"admin user %q still uses a default/known password; rotate it before starting in production",
					u.username,
				)
			}
		}
	}
	return nil
}
