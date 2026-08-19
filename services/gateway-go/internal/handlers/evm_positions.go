package handlers

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// EVM DeFi positions via Moralis' Web3 Data API — protocol-labeled positions
// (Aave supplied, Uniswap LP, Lido staking, …) that Alchemy's raw balances
// can't identify. One call per chain; the majors are queried in parallel.

// moralisChain maps our chain name to Moralis' `chain` query value.
var moralisChains = []struct {
	name  string // display chain
	param string // Moralis chain id
}{
	{"ethereum", "eth"},
	{"base", "base"},
	{"arbitrum", "arbitrum"},
	{"optimism", "optimism"},
	{"polygon", "polygon"},
}

// moralisPosition mirrors one element of GET /wallets/{addr}/defi/positions.
type moralisPosition struct {
	ProtocolName string `json:"protocol_name"`
	ProtocolID   string `json:"protocol_id"`
	ProtocolURL  string `json:"protocol_url"`
	ProtocolLogo string `json:"protocol_logo"`
	Position     struct {
		Label             string   `json:"label"`
		BalanceUsd        float64  `json:"balance_usd"`
		TotalUnclaimedUsd *float64 `json:"total_unclaimed_usd_value"`
		Tokens            []struct {
			Symbol           string `json:"symbol"`
			Name             string `json:"name"`
			TokenType        string `json:"token_type"`
			ContractAddress  string `json:"contract_address"`
			BalanceFormatted string `json:"balance_formatted"`
			Logo             string `json:"logo"`
		} `json:"tokens"`
	} `json:"position"`
}

type evmPositionToken struct {
	Symbol string  `json:"symbol"`
	Type   string  `json:"type"`
	Amount float64 `json:"amount"`
	Logo   string  `json:"logo,omitempty"`
}

type evmPosition struct {
	Chain        string             `json:"chain"`
	Protocol     string             `json:"protocol"`
	ProtocolID   string             `json:"protocolId"`
	ProtocolURL  string             `json:"protocolUrl,omitempty"`
	Logo         string             `json:"logo,omitempty"`
	Label        string             `json:"label"`
	BalanceUsd   float64            `json:"balanceUsd"`
	UnclaimedUsd float64            `json:"unclaimedUsd"`
	Tokens       []evmPositionToken `json:"tokens"`
}

// GetEvmPositions handles GET /market/evm/positions?address=0x.. — the wallet's
// DeFi positions across the major EVM chains, labeled by protocol.
func (m *MarketProxy) GetEvmPositions(w http.ResponseWriter, r *http.Request) {
	if m.moralisAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "EVM positions are not configured")
		return
	}
	address := strings.TrimSpace(r.URL.Query().Get("address"))
	if !looksLikeEVMAddress(address) {
		writeError(w, http.StatusBadRequest, "A valid 0x EVM address is required")
		return
	}
	address = strings.ToLower(address)

	cacheKey := "evm-positions:" + address
	if cached, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write(cached)
		return
	}

	// Fan out one request per chain; a slow or failing chain never blocks the rest.
	var (
		wg  sync.WaitGroup
		mu  sync.Mutex
		all []evmPosition
	)
	for _, ch := range moralisChains {
		wg.Add(1)
		go func(chainName, chainParam string) {
			defer wg.Done()
			positions := m.fetchMoralisPositions(r, address, chainName, chainParam)
			if len(positions) > 0 {
				mu.Lock()
				all = append(all, positions...)
				mu.Unlock()
			}
		}(ch.name, ch.param)
	}
	wg.Wait()

	sort.SliceStable(all, func(i, j int) bool { return all[i].BalanceUsd > all[j].BalanceUsd })
	var totalUsd float64
	for i := range all {
		totalUsd += all[i].BalanceUsd
	}

	out := map[string]any{"address": address, "totalUsd": totalUsd, "positions": all}
	body, err := json.Marshal(out)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to encode positions")
		return
	}
	m.cache.Set(cacheKey, body, 60*time.Second)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

func (m *MarketProxy) fetchMoralisPositions(r *http.Request, address, chainName, chainParam string) []evmPosition {
	url := fmt.Sprintf("https://deep-index.moralis.io/api/v2.2/wallets/%s/defi/positions?chain=%s", address, chainParam)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, url, nil)
	if err != nil {
		return nil
	}
	req.Header.Set("X-API-Key", m.moralisAPIKey)
	req.Header.Set("Accept", "application/json")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Warn("moralis positions error", "chain", chainName, "error", err)
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		slog.Warn("moralis positions non-200", "chain", chainName, "status", resp.StatusCode)
		return nil
	}

	var raw []moralisPosition
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		slog.Warn("moralis positions decode", "chain", chainName, "error", err)
		return nil
	}

	out := make([]evmPosition, 0, len(raw))
	for _, p := range raw {
		if p.Position.BalanceUsd <= 0 && p.Position.TotalUnclaimedUsd == nil {
			continue
		}
		tokens := make([]evmPositionToken, 0, len(p.Position.Tokens))
		for _, t := range p.Position.Tokens {
			amt, _ := strconv.ParseFloat(t.BalanceFormatted, 64)
			tokens = append(tokens, evmPositionToken{
				Symbol: t.Symbol, Type: t.TokenType, Amount: amt, Logo: t.Logo,
			})
		}
		unclaimed := 0.0
		if p.Position.TotalUnclaimedUsd != nil {
			unclaimed = *p.Position.TotalUnclaimedUsd
		}
		out = append(out, evmPosition{
			Chain: chainName, Protocol: p.ProtocolName, ProtocolID: p.ProtocolID,
			ProtocolURL: p.ProtocolURL, Logo: p.ProtocolLogo, Label: p.Position.Label,
			BalanceUsd: p.Position.BalanceUsd, UnclaimedUsd: unclaimed, Tokens: tokens,
		})
	}
	return out
}
