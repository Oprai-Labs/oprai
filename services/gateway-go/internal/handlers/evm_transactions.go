package handlers

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// EVM transaction history via Moralis' decoded wallet history — multichain,
// with per-tx category (swap/send/nft…) and the counterparty ENTITY (Uniswap,
// OpenSea…) so we can say WHICH PLATFORM each tx happened on. Etherscan's free
// tier is Ethereum-only, so Moralis (already integrated) owns this end to end.

// moralisHistoryResponse mirrors GET /wallets/{addr}/history.
type moralisHistoryResponse struct {
	Result []struct {
		Hash            string `json:"hash"`
		FromAddress     string `json:"from_address"`
		ToAddress       string `json:"to_address"`
		BlockTimestamp  string `json:"block_timestamp"`
		ReceiptStatus   string `json:"receipt_status"`
		Category        string `json:"category"`
		Summary         string `json:"summary"`
		MethodLabel     string `json:"method_label"`
		ToAddressLabel  string `json:"to_address_label"`
		ToAddressEntity string `json:"to_address_entity"`
		ToEntityLogo    string `json:"to_address_entity_logo"`
		PossibleSpam    bool   `json:"possible_spam"`
	} `json:"result"`
}

type evmTx struct {
	Hash         string `json:"hash"`
	Chain        string `json:"chain"`
	Timestamp    string `json:"timestamp"`
	Category     string `json:"category"`
	Summary      string `json:"summary"`
	Platform     string `json:"platform,omitempty"`
	PlatformLogo string `json:"platformLogo,omitempty"`
	Direction    string `json:"direction"`
	Success      bool   `json:"success"`
}

// GetEvmTransactions handles GET /market/evm/transactions?address=0x.. — the
// wallet's recent, platform-labeled transactions across the major EVM chains.
func (m *MarketProxy) GetEvmTransactions(w http.ResponseWriter, r *http.Request) {
	if m.moralisAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "EVM transactions are not configured")
		return
	}
	address := strings.TrimSpace(r.URL.Query().Get("address"))
	if !looksLikeEVMAddress(address) {
		writeError(w, http.StatusBadRequest, "A valid 0x EVM address is required")
		return
	}
	address = strings.ToLower(address)

	cacheKey := "evm-txs:" + address
	if cached, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write(cached)
		return
	}

	var (
		wg  sync.WaitGroup
		mu  sync.Mutex
		all []evmTx
	)
	for _, ch := range moralisChains {
		wg.Add(1)
		go func(chainName, chainParam string) {
			defer wg.Done()
			txs := m.fetchMoralisHistory(r, address, chainName, chainParam)
			if len(txs) > 0 {
				mu.Lock()
				all = append(all, txs...)
				mu.Unlock()
			}
		}(ch.name, ch.param)
	}
	wg.Wait()

	// Newest first across all chains; cap so the response stays lightweight.
	sort.SliceStable(all, func(i, j int) bool { return all[i].Timestamp > all[j].Timestamp })
	if len(all) > 40 {
		all = all[:40]
	}

	out := map[string]any{"address": address, "transactions": all}
	body, err := json.Marshal(out)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to encode transactions")
		return
	}
	m.cache.Set(cacheKey, body, 60*time.Second)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

func (m *MarketProxy) fetchMoralisHistory(r *http.Request, address, chainName, chainParam string) []evmTx {
	url := fmt.Sprintf("https://deep-index.moralis.io/api/v2.2/wallets/%s/history?chain=%s&limit=15&order=DESC", address, chainParam)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, url, nil)
	if err != nil {
		return nil
	}
	req.Header.Set("X-API-Key", m.moralisAPIKey)
	req.Header.Set("Accept", "application/json")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Warn("moralis history error", "chain", chainName, "error", err)
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		slog.Warn("moralis history non-200", "chain", chainName, "status", resp.StatusCode)
		return nil
	}

	var parsed moralisHistoryResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		slog.Warn("moralis history decode", "chain", chainName, "error", err)
		return nil
	}

	out := make([]evmTx, 0, len(parsed.Result))
	for _, t := range parsed.Result {
		if t.PossibleSpam {
			continue
		}
		// Platform = the labeled counterparty entity (Uniswap, OpenSea…), falling
		// back to any address label Moralis attached.
		platform := t.ToAddressEntity
		if platform == "" {
			platform = t.ToAddressLabel
		}
		direction := "in"
		if strings.EqualFold(t.FromAddress, address) {
			direction = "out"
		}
		out = append(out, evmTx{
			Hash: t.Hash, Chain: chainName, Timestamp: t.BlockTimestamp,
			Category: t.Category, Summary: t.Summary, Platform: platform,
			PlatformLogo: t.ToEntityLogo, Direction: direction,
			Success: t.ReceiptStatus == "1",
		})
	}
	return out
}
