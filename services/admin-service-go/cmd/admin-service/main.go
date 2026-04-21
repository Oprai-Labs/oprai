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

	"github.com/joho/godotenv"

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

	queries := db.NewQueries(pool)

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
