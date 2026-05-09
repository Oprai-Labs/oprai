package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/joho/godotenv"
	"github.com/redis/go-redis/v9"

	"github.com/oprai/oprai/services/gateway-go/internal/config"
	"github.com/oprai/oprai/services/gateway-go/internal/proxy"
	"github.com/oprai/oprai/services/gateway-go/internal/server"
)

func main() {
	// Load .env from repo root (best-effort, not fatal if missing)
	_ = godotenv.Load("../../.env")

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	cfg := config.Load()

	// Validate JWT secret
	const insecureJWTDefault = "dev-insecure-secret-change"
	isProd := cfg.Environment == "production"
	if cfg.JWTSecret == insecureJWTDefault {
		if isProd {
			slog.Error("OPRAI_JWT_SECRET is set to the insecure default, refusing to start in production")
			os.Exit(1)
		}
		slog.Warn("Using insecure default JWT secret, set OPRAI_JWT_SECRET before going to production")
	} else if len(cfg.JWTSecret) < 32 {
		if isProd {
			slog.Error("OPRAI_JWT_SECRET must be at least 32 characters, refusing to start in production")
			os.Exit(1)
		}
		slog.Warn("OPRAI_JWT_SECRET is shorter than 32 characters, use a longer secret in production")
	}

	// Validate internal API key
	const insecureAPIKeyDefault = "dev-internal-key-change"
	if cfg.InternalAPIKey == insecureAPIKeyDefault {
		if isProd {
		slog.Error("OPRAI_INTERNAL_API_KEY is set to the insecure default, refusing to start in production")
			os.Exit(1)
		}
		slog.Warn("Using insecure default internal API key, set OPRAI_INTERNAL_API_KEY before going to production")
	} else if len(cfg.InternalAPIKey) < 16 {
		if isProd {
			slog.Error("OPRAI_INTERNAL_API_KEY must be at least 16 characters, refusing to start in production")
			os.Exit(1)
		}
		slog.Warn("OPRAI_INTERNAL_API_KEY is shorter than 16 characters, use a longer key in production")
	}

	// Root context — cancelled on shutdown to stop background goroutines
	// (rate-limiter cleanup, cache cleanup).
	rootCtx, rootCancel := context.WithCancel(context.Background())
	defer rootCancel()

	// Initialize gRPC client connections with circuit breakers
	grpcClients, err := proxy.NewGRPCClients(cfg)
	if err != nil {
		slog.Error("failed to initialize gRPC clients", "error", err)
		os.Exit(1)
	}
	defer grpcClients.Close()

	// Initialize Redis client for the JWT revocation blocklist. We connect
	// best-effort — if Redis is unreachable in dev, the blocklist degrades to
	// in-memory only. In production, the URL must resolve and the ping must
	// succeed; otherwise we refuse to start (revoked tokens would silently come
	// back to life on restart).
	var rdb *redis.Client
	if cfg.RedisURL != "" {
		opts, parseErr := redis.ParseURL(cfg.RedisURL)
		if parseErr != nil {
			slog.Error("invalid REDIS_URL", "error", parseErr)
			os.Exit(1)
		}
		client := redis.NewClient(opts)
		pingCtx, pingCancel := context.WithTimeout(rootCtx, 2*time.Second)
		if pingErr := client.Ping(pingCtx).Err(); pingErr != nil {
			pingCancel()
			if isProd {
				slog.Error("Redis unreachable, refusing to start in production",
					"redis_url", cfg.RedisURL, "error", pingErr)
				os.Exit(1)
			}
			slog.Warn("Redis unreachable — JWT blocklist will be in-memory only",
				"redis_url", cfg.RedisURL, "error", pingErr)
			_ = client.Close()
		} else {
			pingCancel()
			rdb = client
			slog.Info("Redis connected for JWT blocklist", "redis_url", cfg.RedisURL)
			defer func() { _ = client.Close() }()
		}
	}

	// Create HTTP router
	router := server.NewRouter(rootCtx, cfg, grpcClients, rdb)

	httpServer := &http.Server{
		Addr:         fmt.Sprintf(":%d", cfg.Port),
		Handler:      router,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 60 * time.Second, // Longer for SSE streaming
		IdleTimeout:  120 * time.Second,
	}

	// Start HTTP server
	errCh := make(chan error, 1)
	go func() {
		slog.Info("Gateway HTTP server starting",
			"port", cfg.Port,
			"env", cfg.Environment,
			"cors_origin", cfg.CORSOrigin,
			"auth_grpc", cfg.AuthServiceGRPC,
			"chat_grpc", cfg.ChatServiceGRPC,
			"solana_grpc", cfg.SolanaServiceGRPC,
			"memory_grpc", cfg.MemoryServiceGRPC,
		)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- fmt.Errorf("HTTP server error: %w", err)
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

	// Cancel root context to stop background goroutines (rate-limiter cleanup etc.)
	rootCancel()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if err := httpServer.Shutdown(ctx); err != nil {
		slog.Error("HTTP server shutdown error", "error", err)
	}

	slog.Info("Gateway stopped")
}
