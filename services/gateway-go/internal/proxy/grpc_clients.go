package proxy

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"

	"github.com/oprai/oprai/services/gateway-go/internal/config"
)

// GRPCClients holds all gRPC client connections to backend services.
type GRPCClients struct {
	AuthConn   *grpc.ClientConn
	ChatConn   *grpc.ClientConn
	SolanaConn *grpc.ClientConn
	MemoryConn *grpc.ClientConn
}

// NewGRPCClients initializes gRPC connections to all backend services with circuit breakers.
func NewGRPCClients(cfg *config.Config) (*GRPCClients, error) {
	dialOpts := []grpc.DialOption{
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithKeepaliveParams(keepalive.ClientParameters{
			Time:                5 * time.Minute,  // Send ping every 5min (server default min is 5min)
			Timeout:             20 * time.Second, // Wait 20s for ping response
			PermitWithoutStream: false,            // Only ping when there are active streams
		}),
		grpc.WithDefaultCallOptions(
			grpc.MaxCallRecvMsgSize(10 * 1024 * 1024), // 10MB
		),
	}

	clients := &GRPCClients{}
	var err error

	clients.AuthConn, err = dialWithTimeout(cfg.AuthServiceGRPC, dialOpts)
	if err != nil {
		return nil, fmt.Errorf("auth-service gRPC: %w", err)
	}

	clients.ChatConn, err = dialWithTimeout(cfg.ChatServiceGRPC, dialOpts)
	if err != nil {
		clients.AuthConn.Close()
		return nil, fmt.Errorf("chat-service gRPC: %w", err)
	}

	clients.SolanaConn, err = dialWithTimeout(cfg.SolanaServiceGRPC, dialOpts)
	if err != nil {
		clients.AuthConn.Close()
		clients.ChatConn.Close()
		return nil, fmt.Errorf("solana-service gRPC: %w", err)
	}

	clients.MemoryConn, err = dialWithTimeout(cfg.MemoryServiceGRPC, dialOpts)
	if err != nil {
		clients.AuthConn.Close()
		clients.ChatConn.Close()
		clients.SolanaConn.Close()
		return nil, fmt.Errorf("memory-service gRPC: %w", err)
	}

	slog.Info("gRPC clients initialized",
		"auth", cfg.AuthServiceGRPC,
		"chat", cfg.ChatServiceGRPC,
		"solana", cfg.SolanaServiceGRPC,
		"memory", cfg.MemoryServiceGRPC,
	)

	return clients, nil
}

// Close closes all gRPC connections.
func (c *GRPCClients) Close() {
	if c.AuthConn != nil {
		c.AuthConn.Close()
	}
	if c.ChatConn != nil {
		c.ChatConn.Close()
	}
	if c.SolanaConn != nil {
		c.SolanaConn.Close()
	}
	if c.MemoryConn != nil {
		c.MemoryConn.Close()
	}
}

// ServiceHealth holds the health status of a backend service.
type ServiceHealth struct {
	Name           string `json:"name"`
	Status         string `json:"status"` // "ok", "down", "unknown"
	ResponseTimeMs int64  `json:"responseTimeMs,omitempty"`
}

// CheckHealth performs a gRPC health check on all backend services.
func (c *GRPCClients) CheckHealth(ctx context.Context) []ServiceHealth {
	type result struct {
		health ServiceHealth
	}

	services := []struct {
		name string
		conn *grpc.ClientConn
	}{
		{"auth-service", c.AuthConn},
		{"chat-service", c.ChatConn},
		{"solana-service", c.SolanaConn},
		{"memory-service", c.MemoryConn},
	}

	results := make([]ServiceHealth, len(services))
	done := make(chan result, len(services))

	for i, svc := range services {
		go func(idx int, name string, conn *grpc.ClientConn) {
			start := time.Now()
			status := "ok"

			// Check the gRPC connection state
			state := conn.GetState()
			switch state.String() {
			case "READY", "IDLE":
				status = "ok"
			case "CONNECTING":
				status = "unknown"
			default:
				status = "down"
			}

			done <- result{
				health: ServiceHealth{
					Name:           name,
					Status:         status,
					ResponseTimeMs: time.Since(start).Milliseconds(),
				},
			}
		}(i, svc.name, svc.conn)
	}

	for i := 0; i < len(services); i++ {
		r := <-done
		for j, svc := range services {
			if svc.name == r.health.Name {
				results[j] = r.health
				break
			}
		}
	}

	return results
}

func dialWithTimeout(target string, opts []grpc.DialOption) (*grpc.ClientConn, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	conn, err := grpc.DialContext(ctx, target, opts...)
	if err != nil {
		return nil, fmt.Errorf("failed to dial %s: %w", target, err)
	}

	return conn, nil
}
