package handlers

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	"github.com/oprai/oprai/services/gateway-go/internal/middleware"
)

// LiquidationEntry is a normalized liquidation position returned to the frontend.
type LiquidationEntry struct {
	Protocol    string  `json:"protocol"`
	Market      string  `json:"market"`
	HealthRatio float64 `json:"healthRatio"`
}

// LiquidationProxy fetches liquidation data from multiple DeFi protocols on
// behalf of the authenticated user. Keeping this server-side prevents the user's
// wallet address from appearing in third-party server logs and browser extension traces.
type LiquidationProxy struct {
	httpClient *http.Client
}

// NewLiquidationProxy creates a new LiquidationProxy.
func NewLiquidationProxy() *LiquidationProxy {
	return &LiquidationProxy{
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
}

// GetPositions handles GET /defi/liquidations.
// Fetches health ratios from Kamino and Solend for the authenticated wallet.
func (p *LiquidationProxy) GetPositions(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.GetWallet(r.Context())
	if wallet == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprintf(w, `{"error":"authentication required"}`)
		return
	}

	type result struct {
		entries []LiquidationEntry
		err     error
	}

	kaminoCh := make(chan result, 1)
	solendCh := make(chan result, 1)

	go func() {
		e, err := p.fetchKamino(wallet)
		kaminoCh <- result{entries: e, err: err}
	}()
	go func() {
		e, err := p.fetchSolend(wallet)
		solendCh <- result{entries: e, err: err}
	}()

	var positions []LiquidationEntry
	for _, ch := range []chan result{kaminoCh, solendCh} {
		res := <-ch
		if res.err != nil {
			slog.Warn("liquidation proxy fetch error", "error", res.err)
			continue
		}
		positions = append(positions, res.entries...)
	}

	if positions == nil {
		positions = []LiquidationEntry{} // return [] not null
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(map[string]any{"positions": positions}); err != nil {
		slog.Error("failed to encode liquidation response", "error", err)
	}
}

func (p *LiquidationProxy) fetchKamino(wallet string) ([]LiquidationEntry, error) {
	url := fmt.Sprintf(
		"https://api.kamino.finance/kamino-market/7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF/obligations?wallet=%s",
		wallet,
	)
	resp, err := p.httpClient.Get(url)
	if err != nil {
		return nil, fmt.Errorf("kamino fetch: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, nil
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20)) // 1 MB limit
	if err != nil {
		return nil, fmt.Errorf("kamino read: %w", err)
	}

	var obligations []map[string]json.RawMessage
	if err := json.Unmarshal(body, &obligations); err != nil {
		return nil, nil
	}

	var entries []LiquidationEntry
	for _, obl := range obligations {
		health, market := extractKaminoFields(obl)
		if health <= 0 {
			continue
		}
		entries = append(entries, LiquidationEntry{Protocol: "kamino", Market: market, HealthRatio: health})
	}
	return entries, nil
}

func extractKaminoFields(obl map[string]json.RawMessage) (health float64, market string) {
	for _, key := range []string{"loanToValue", "healthFactor"} {
		if raw, ok := obl[key]; ok {
			if err := json.Unmarshal(raw, &health); err == nil && health > 0 {
				break
			}
		}
	}
	if raw, ok := obl["market"]; ok {
		_ = json.Unmarshal(raw, &market)
	}
	if market == "" {
		market = "Kamino"
	}
	return
}

func (p *LiquidationProxy) fetchSolend(wallet string) ([]LiquidationEntry, error) {
	url := fmt.Sprintf("https://api.solend.fi/v1/obligation-metadata/%s", wallet)
	resp, err := p.httpClient.Get(url)
	if err != nil {
		return nil, fmt.Errorf("solend fetch: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, nil
	}

	var data struct {
		Results []struct {
			LiquidationThreshold float64 `json:"liquidationThreshold"`
			MarketName           string  `json:"marketName"`
		} `json:"results"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&data); err != nil {
		return nil, nil
	}

	var entries []LiquidationEntry
	for _, obl := range data.Results {
		if obl.LiquidationThreshold <= 0 {
			continue
		}
		market := obl.MarketName
		if market == "" {
			market = "Solend"
		}
		entries = append(entries, LiquidationEntry{Protocol: "solend", Market: market, HealthRatio: obl.LiquidationThreshold})
	}
	return entries, nil
}
