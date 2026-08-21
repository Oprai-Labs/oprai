package handlers

import "testing"

func supportOf(t *testing.T, chain string) evmChainSupport {
	t.Helper()
	for _, c := range evmChainSupportTable() {
		if c.Chain == chain {
			return c
		}
	}
	t.Fatalf("%s is read by a provider but missing from the support table", chain)
	return evmChainSupport{}
}

func TestEveryChainAReaderCoversIsListed(t *testing.T) {
	// The drift this exists to stop: a chain added to a reader and never
	// advertised, or advertised long after it stopped being read.
	for _, n := range evmNetworks {
		supportOf(t, n.chain)
	}
	for _, c := range moralisChains {
		supportOf(t, c.name)
	}
	supportOf(t, robinhoodChain)
}

func TestCoverageIsReportedPerSurfaceNotAsOneWord(t *testing.T) {
	// Coverage is not uniform, and "supported" would flatten it. Robinhood
	// Chain has no DeFi-position provider; saying otherwise sends someone
	// looking for a positions view that was never built.
	rh := supportOf(t, robinhoodChain)
	for _, r := range rh.Reads {
		if r == "positions" {
			t.Fatal("robinhood has no positions provider but claims one")
		}
	}
	eth := supportOf(t, "ethereum")
	want := map[string]bool{"balances": true, "nfts": true, "positions": true, "transactions": true}
	if len(eth.Reads) != len(want) {
		t.Fatalf("ethereum reads = %v, want all four surfaces", eth.Reads)
	}
	for _, r := range eth.Reads {
		if !want[r] {
			t.Fatalf("ethereum claims unknown surface %q", r)
		}
	}
}

func TestNoChainIsListedWithoutANativeSymbol(t *testing.T) {
	// A chain with no native symbol is one that reached the table through a
	// reader that never declared what it reads in — a sign the entry was
	// guessed rather than derived.
	for _, c := range evmChainSupportTable() {
		if c.NativeSymbol == "" {
			t.Fatalf("%s is listed with no native symbol", c.Chain)
		}
		if c.Label == c.Chain {
			t.Fatalf("%s has no display label", c.Chain)
		}
	}
}

func TestTheMostCompleteChainsComeFirst(t *testing.T) {
	table := evmChainSupportTable()
	for i := 1; i < len(table); i++ {
		if len(table[i].Reads) > len(table[i-1].Reads) {
			t.Fatalf("%s (%d surfaces) is listed after %s (%d)",
				table[i].Chain, len(table[i].Reads),
				table[i-1].Chain, len(table[i-1].Reads))
		}
	}
}
