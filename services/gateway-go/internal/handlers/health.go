package handlers

import (
	"log/slog"
	"net/http"

	"github.com/oprai/oprai/services/gateway-go/internal/proxy"
)

// HealthHandler handles aggregated health check endpoints.
type HealthHandler struct {
	clients *proxy.GRPCClients
}

// NewHealthHandler creates a new HealthHandler.
func NewHealthHandler(clients *proxy.GRPCClients) *HealthHandler {
	return &HealthHandler{clients: clients}
}

// AggregatedHealth returns the health status of the gateway and all backend services.
func (h *HealthHandler) AggregatedHealth(w http.ResponseWriter, r *http.Request) {
	serviceHealths := h.clients.CheckHealth(r.Context())

	allOk := true
	allDown := true
	for _, sh := range serviceHealths {
		if sh.Status != "ok" {
			allOk = false
		}
		if sh.Status != "down" {
			allDown = false
		}
	}

	status := "degraded"
	if allOk {
		status = "ok"
	} else if allDown {
		status = "down"
	}

	if status == "degraded" || status == "down" {
		downServices := make([]string, 0)
		for _, sh := range serviceHealths {
			if sh.Status != "ok" {
				downServices = append(downServices, sh.Name)
			}
		}
		slog.Warn("Backend health check returned non-healthy status",
			"status", status,
			"unhealthy_services", downServices,
		)
	}

	httpStatus := http.StatusOK
	if status == "down" {
		httpStatus = http.StatusServiceUnavailable
	}

	writeJSON(w, httpStatus, map[string]interface{}{
		"status":   status,
		"services": serviceHealths,
	})
}

