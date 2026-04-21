package server

import (
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"

	"github.com/oprai/oprai/services/admin-service-go/internal/config"
	"github.com/oprai/oprai/services/admin-service-go/internal/db"
)

// NewGRPCServer creates and configures the gRPC server for the admin service.
// The admin service primarily exposes an HTTP API; the gRPC server provides
// health checking and a future extension point for inter-service communication.
func NewGRPCServer(cfg *config.Config, queries *db.Queries) *grpc.Server {
	// cfg and queries are reserved for future admin gRPC handlers (e.g. session management).
	// Currently the admin service exposes HTTP only; gRPC provides health + reflection.
	_ = cfg
	_ = queries

	s := grpc.NewServer()

	// Register health check service
	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(s, healthServer)
	healthServer.SetServingStatus("admin-service", healthpb.HealthCheckResponse_SERVING)

	// Enable reflection for debugging with grpcurl
	reflection.Register(s)

	return s
}
