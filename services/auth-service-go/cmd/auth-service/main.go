package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"github.com/redis/go-redis/v9"

	"github.com/oprai/oprai/services/auth-service-go/internal/audit"
	"github.com/oprai/oprai/services/auth-service-go/internal/config"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/server"
	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

func main() {
	// Initialize structured JSON logger (matches gateway and admin services)
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	// Load .env from repo root (best-effort, not fatal if missing)
	_ = godotenv.Load("../../.env")

	slog.Info("Auth service starting")

	// ── Load configuration ────────────────────────────────────────────
	cfg, err := config.Load()
	if err != nil {
		slog.Error("Configuration error", "error", err)
		os.Exit(1)
	}

	// ── Root context for graceful shutdown ─────────────────────────────
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// ── Initialize PostgreSQL connection pool ─────────────────────────
	pool, err := db.NewPool(ctx, cfg.DatabaseURL, cfg.DBSchema)
	if err != nil {
		slog.Error("Database connection failed", "error", err)
		os.Exit(1)
	}
	defer pool.Close()

	queries := db.NewQueries(pool, cfg.DBSchema)

	// ── Initialize Redis ──────────────────────────────────────────────
	var rdb *redis.Client
	if cfg.RedisURL != "" {
		opt, err := redis.ParseURL(cfg.RedisURL)
		if err != nil {
			slog.Warn("Failed to parse REDIS_URL, nonces will use in-memory fallback",
				"redis_url", cfg.RedisURL,
				"error", err,
			)
		} else {
			rdb = redis.NewClient(opt)

			// Verify Redis connectivity
			pingCtx, pingCancel := context.WithTimeout(ctx, 3*time.Second)
			defer pingCancel()

			if err := rdb.Ping(pingCtx).Err(); err != nil {
				slog.Warn("Redis ping failed, nonces will use in-memory fallback", "error", err)
				rdb.Close()
				rdb = nil
			} else {
				slog.Info("Connected to Redis")
				defer rdb.Close()
			}
		}
	}

	// ── Initialize services ───────────────────────────────────────────
	jwtService := services.NewJWTService(cfg.JWTSecret, cfg.JWTPreviousSecret, cfg.SessionTTLSeconds)
	nonceService := services.NewNonceService(rdb, cfg.NonceTTLSeconds)
	revocationService := services.NewRevocationService(rdb)

	// ── Start audit event worker ──────────────────────────────────────
	auditLogger := audit.New(queries)
	auditLogger.StartWorker(ctx)
	slog.Info("Audit event worker started")

	// ── Start gRPC server ─────────────────────────────────────────────
	grpcServer := server.NewGRPCServer(cfg, jwtService, nonceService, queries)
	go func() {
		if err := grpcServer.Serve(); err != nil {
			slog.Error("gRPC server error", "error", err)
		}
	}()

	// ── Start HTTP server ─────────────────────────────────────────────
	httpServer := server.NewHTTPServer(cfg, jwtService, nonceService, revocationService, queries, auditLogger)

	go func() {
		slog.Info("HTTP server listening", "addr", httpServer.Addr)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("HTTP server error", "error", err)
			os.Exit(1)
		}
	}()

	slog.Info("Auth service started",
		"http_port", cfg.Port,
		"grpc_port", cfg.GRPCPort,
	)

	// ── Wait for shutdown signal ──────────────────────────────────────
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	sig := <-quit

	slog.Info("Received shutdown signal, shutting down", "signal", sig.String())

	// ── Graceful shutdown ─────────────────────────────────────────────
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer shutdownCancel()

	// Shutdown HTTP server
	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		slog.Error("HTTP server shutdown error", "error", err)
	}

	// Shutdown gRPC server
	grpcServer.GracefulStop()

	// Cancel root context
	cancel()

	slog.Info("Auth service stopped")
}

