package server

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"

	"github.com/oprai/oprai/services/auth-service-go/internal/config"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

// GRPCServer wraps a gRPC server with auth and user service implementations.
type GRPCServer struct {
	server       *grpc.Server
	port         int
	jwtService   *services.JWTService
	nonceService *services.NonceService
	queries      *db.Queries
}

// NewGRPCServer creates and configures a gRPC server.
func NewGRPCServer(
	cfg *config.Config,
	jwtService *services.JWTService,
	nonceService *services.NonceService,
	queries *db.Queries,
) *GRPCServer {
	server := grpc.NewServer(
		grpc.UnaryInterceptor(loggingInterceptor),
	)

	gs := &GRPCServer{
		server:       server,
		port:         cfg.GRPCPort,
		jwtService:   jwtService,
		nonceService: nonceService,
		queries:      queries,
	}

	// Register health check service
	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(server, healthServer)
	healthServer.SetServingStatus("oprai.auth.AuthService", healthpb.HealthCheckResponse_SERVING)
	healthServer.SetServingStatus("oprai.auth.UserService", healthpb.HealthCheckResponse_SERVING)

	// Enable reflection for debugging tools like grpcurl
	reflection.Register(server)

	slog.Info("gRPC server configured", "port", cfg.GRPCPort)
	return gs
}

// Serve starts the gRPC server on the configured port.
func (gs *GRPCServer) Serve() error {
	lis, err := net.Listen("tcp", fmt.Sprintf(":%d", gs.port))
	if err != nil {
		return fmt.Errorf("gRPC listen: %w", err)
	}

	slog.Info("gRPC server listening", "port", gs.port)
	return gs.server.Serve(lis)
}

// GracefulStop gracefully stops the gRPC server.
func (gs *GRPCServer) GracefulStop() {
	gs.server.GracefulStop()
}

// ValidateToken is a gRPC-callable method for inter-service token validation.
// Other Go services can call this to validate JWTs without duplicating logic.
func (gs *GRPCServer) ValidateToken(ctx context.Context, tokenString string) (wallet string, expiresAt time.Time, err error) {
	wallet, expiresAt, err = gs.jwtService.Validate(tokenString)
	if err != nil {
		return "", time.Time{}, status.Errorf(codes.Unauthenticated, "invalid token: %v", err)
	}
	return wallet, expiresAt, nil
}

// GetUser is a gRPC-callable method for inter-service user lookup.
func (gs *GRPCServer) GetUser(ctx context.Context, walletAddress string) (*db.User, error) {
	user, err := gs.queries.GetUserByWallet(ctx, walletAddress)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to get user: %v", err)
	}
	if user == nil {
		return nil, status.Errorf(codes.NotFound, "user not found")
	}
	return user, nil
}

// loggingInterceptor logs gRPC method calls with structured fields.
func loggingInterceptor(
	ctx context.Context,
	req any,
	info *grpc.UnaryServerInfo,
	handler grpc.UnaryHandler,
) (any, error) {
	start := time.Now()
	resp, err := handler(ctx, req)
	duration := time.Since(start)

	if err != nil {
		slog.Error("gRPC call failed",
			"method", info.FullMethod,
			"duration_ms", duration.Milliseconds(),
			"error", err,
		)
	} else {
		slog.Debug("gRPC call completed",
			"method", info.FullMethod,
			"duration_ms", duration.Milliseconds(),
		)
	}

	return resp, err
}
