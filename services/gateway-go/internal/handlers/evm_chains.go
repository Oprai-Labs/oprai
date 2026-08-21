package handlers

import (
	"net/http"
	"sort"
)

// Which chains this product can actually see.
//
// Two questions get mistaken for each other. One is where assets can be
// *sent* — the bridge routes to sixty-odd chains, most of which we have never
// read a wallet on. The other is where this product can look at a wallet,
// price what is in it, and act. Answering the first when asked the second
// offers a portfolio that does not exist, and that is exactly what the help
// text did.
//
// So this is assembled from the readers themselves. Every chain here is a
// chain some reader loops over; add a chain to a reader and it appears, drop
// one and it goes. There is no list of chain names to keep in step.

type evmChainSupport struct {
	Chain        string   `json:"chain"`
	Label        string   `json:"label"`
	NativeSymbol string   `json:"nativeSymbol"`
	// balances / nfts / positions / transactions — named individually because
	// coverage is not uniform: Robinhood Chain has no DeFi-position provider,
	// and saying "supported" would imply one.
	Reads []string `json:"reads"`
}

// Display names. The only hand-written part, and it decides nothing.
var evmChainLabels = map[string]string{
	"ethereum":  "Ethereum",
	"base":      "Base",
	"arbitrum":  "Arbitrum",
	"optimism":  "Optimism",
	"polygon":   "Polygon",
	"bsc":       "BNB Chain",
	"robinhood": "Robinhood Chain",
}

func evmChainSupportTable() []evmChainSupport {
	surfaces := map[string]map[string]bool{}
	native := map[string]string{}
	add := func(chain, surface string) {
		if surfaces[chain] == nil {
			surfaces[chain] = map[string]bool{}
		}
		surfaces[chain][surface] = true
	}

	// Alchemy — token and native balances, and NFTs.
	for _, n := range evmNetworks {
		add(n.chain, "balances")
		add(n.chain, "nfts")
		native[n.chain] = n.nativeSymbol
	}
	// Moralis — DeFi positions and labelled transaction history.
	for _, c := range moralisChains {
		add(c.name, "positions")
		add(c.name, "transactions")
	}
	// Robinhood Chain is on neither provider; it is read through its own
	// Blockscout instance, which is why it comes from a third place.
	for _, s := range robinhoodReads {
		add(robinhoodChain, s)
	}
	native[robinhoodChain] = robinhoodNativeSymbol

	out := make([]evmChainSupport, 0, len(surfaces))
	for chain, set := range surfaces {
		reads := make([]string, 0, len(set))
		for s := range set {
			reads = append(reads, s)
		}
		sort.Strings(reads)
		label := evmChainLabels[chain]
		if label == "" {
			label = chain
		}
		out = append(out, evmChainSupport{
			Chain:        chain,
			Label:        label,
			NativeSymbol: native[chain],
			Reads:        reads,
		})
	}
	// Most-covered first, then by name, so the answer opens with the chains
	// where the product is whole rather than in provider order.
	sort.Slice(out, func(i, j int) bool {
		if len(out[i].Reads) != len(out[j].Reads) {
			return len(out[i].Reads) > len(out[j].Reads)
		}
		return out[i].Chain < out[j].Chain
	})
	return out
}

// GetEvmChains lists the non-Solana chains this product reads wallets on.
// Public: it is a description of ourselves, costs no upstream call, and is
// what /help is built from.
func (m *MarketProxy) GetEvmChains(w http.ResponseWriter, r *http.Request) {
	chains := evmChainSupportTable()
	writeJSON(w, http.StatusOK, map[string]any{
		"chains": chains,
		"count":  len(chains),
	})
}
