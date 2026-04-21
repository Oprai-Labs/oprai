package handlers

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
)

// PumpFunProxy proxies requests to the pump.fun public data API.
// All data is fetched from https://frontend-api.pump.fun and returned as-is.
type PumpFunProxy struct {
	client  *http.Client
	dataAPI string // pump.fun frontend data API
}

// NewPumpFunProxy creates a PumpFunProxy.
func NewPumpFunProxy() *PumpFunProxy {
	return &PumpFunProxy{
		client:  &http.Client{Timeout: 15 * time.Second},
		dataAPI: "https://frontend-api.pump.fun",
	}
}

// ─── Token Data ───────────────────────────────────────────────────────────────

// GetToken handles GET /pumpfun/token/{mint}
// Returns full token info including bonding curve state and graduation status.
func (p *PumpFunProxy) GetToken(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint address required")
		return
	}
	p.proxyGet(w, r, fmt.Sprintf("%s/coins/%s", p.dataAPI, mint))
}

// ListTokens handles GET /pumpfun/tokens
// Query params: limit (default 50), offset (default 0), sort (default last_trade_timestamp)
func (p *PumpFunProxy) ListTokens(w http.ResponseWriter, r *http.Request) {
	q := buildTokenQuery(r, "last_trade_timestamp", "DESC")
	p.proxyGet(w, r, fmt.Sprintf("%s/coins?%s", p.dataAPI, q))
}

// GetTrending handles GET /pumpfun/tokens/trending
// Returns tokens sorted by market cap descending.
func (p *PumpFunProxy) GetTrending(w http.ResponseWriter, r *http.Request) {
	q := buildTokenQuery(r, "market_cap", "DESC")
	p.proxyGet(w, r, fmt.Sprintf("%s/coins?%s", p.dataAPI, q))
}

// GetNew handles GET /pumpfun/tokens/new
// Returns the most recently created tokens.
func (p *PumpFunProxy) GetNew(w http.ResponseWriter, r *http.Request) {
	q := buildTokenQuery(r, "created_timestamp", "DESC")
	p.proxyGet(w, r, fmt.Sprintf("%s/coins?%s", p.dataAPI, q))
}

// GetGraduating handles GET /pumpfun/tokens/graduating
// Returns tokens with a high bonding-curve fill rate (close to migration).
// pump.fun doesn't have a dedicated endpoint; we filter by reply_count as a proxy,
// and the frontend can further filter by virtual_sol_reserves ratio.
func (p *PumpFunProxy) GetGraduating(w http.ResponseWriter, r *http.Request) {
	q := buildTokenQuery(r, "last_reply", "DESC")
	p.proxyGet(w, r, fmt.Sprintf("%s/coins?%s", p.dataAPI, q))
}

// SearchTokens handles GET /pumpfun/tokens/search?q=...
func (p *PumpFunProxy) SearchTokens(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query().Get("q")
	if query == "" {
		writeError(w, http.StatusBadRequest, "q parameter required")
		return
	}
	limit := clampInt(r.URL.Query().Get("limit"), 20, 1, 50)
	encoded := url.QueryEscape(query)
	p.proxyGet(w, r, fmt.Sprintf("%s/coins/search?q=%s&limit=%d&includeNsfw=false", p.dataAPI, encoded, limit))
}

// GetKingOfHill handles GET /pumpfun/koth
// Returns the current King of the Hill token.
func (p *PumpFunProxy) GetKingOfHill(w http.ResponseWriter, r *http.Request) {
	p.proxyGet(w, r, fmt.Sprintf("%s/coins/king-of-the-hill?includeNsfw=false", p.dataAPI))
}

// GetTokenComments handles GET /pumpfun/token/{mint}/comments
// Returns community replies/comments for a token.
// Query params: limit (default 25), offset (default 0)
func (p *PumpFunProxy) GetTokenComments(w http.ResponseWriter, r *http.Request) {
	mint := chi.URLParam(r, "mint")
	if mint == "" {
		writeError(w, http.StatusBadRequest, "mint address required")
		return
	}
	limit := clampInt(r.URL.Query().Get("limit"), 25, 1, 100)
	offset := clampInt(r.URL.Query().Get("offset"), 0, 0, 10000)
	p.proxyGet(w, r, fmt.Sprintf("%s/replies/%s?limit=%d&offset=%d", p.dataAPI, mint, limit, offset))
}

// GetUserProfile handles GET /pumpfun/user/{wallet}
// Returns a user's pump.fun profile including tokens created and held.
func (p *PumpFunProxy) GetUserProfile(w http.ResponseWriter, r *http.Request) {
	wallet := chi.URLParam(r, "wallet")
	if wallet == "" {
		writeError(w, http.StatusBadRequest, "wallet address required")
		return
	}
	p.proxyGet(w, r, fmt.Sprintf("%s/users/%s", p.dataAPI, wallet))
}

// ─── Internal proxy helper ────────────────────────────────────────────────────

func (p *PumpFunProxy) proxyGet(w http.ResponseWriter, r *http.Request, targetURL string) {
	req, err := http.NewRequestWithContext(r.Context(), "GET", targetURL, nil)
	if err != nil {
		slog.Error("PumpFun proxy: failed to create request", "url", targetURL, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to build upstream request")
		return
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "oprai-gateway/1.0")

	resp, err := p.client.Do(req)
	if err != nil {
		slog.Error("PumpFun proxy: upstream request failed", "url", targetURL, "error", err)
		writeError(w, http.StatusBadGateway, "Pump.fun API unreachable")
		return
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "Failed to read upstream response")
		return
	}

	// Verify it's valid JSON before forwarding (guards against HTML error pages)
	if !json.Valid(body) {
		slog.Warn("PumpFun proxy: non-JSON response", "url", targetURL, "status", resp.StatusCode)
		writeError(w, http.StatusBadGateway, fmt.Sprintf("Upstream returned non-JSON (status %d)", resp.StatusCode))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	_, _ = w.Write(body)
}

// ─── Query-building helpers ───────────────────────────────────────────────────

func buildTokenQuery(r *http.Request, defaultSort, defaultOrder string) string {
	q := r.URL.Query()
	limit := clampInt(q.Get("limit"), 50, 1, 100)
	offset := clampInt(q.Get("offset"), 0, 0, 10000)
	sort := q.Get("sort")
	if sort == "" {
		sort = defaultSort
	}
	order := strings.ToUpper(q.Get("order"))
	if order != "ASC" && order != "DESC" {
		order = defaultOrder
	}
	includeNsfw := q.Get("includeNsfw") == "true"

	return fmt.Sprintf("limit=%d&offset=%d&sort=%s&order=%s&includeNsfw=%s",
		limit, offset,
		url.QueryEscape(sort),
		order,
		strconv.FormatBool(includeNsfw),
	)
}

func clampInt(s string, def, min, max int) int {
	n, err := strconv.Atoi(s)
	if err != nil || n < min {
		if s != "" && err == nil {
			return min
		}
		return def
	}
	if n > max {
		return max
	}
	return n
}
