package handlers

import "testing"

func TestNormalizeHeliusTx(t *testing.T) {
	t.Parallel()

	item := normalizeHeliusTx(heliusTx{
		Signature:   "sig1",
		Timestamp:   1700000000,
		Type:        "SWAP",
		Source:      "JUPITER",
		Description: "Swap SOL for USDC",
		Fee:         5000,
		NativeTransfers: []heliusTransfer{
			{
				Amount:          1_000_000_000,
				FromUserAccount: "wallet_a",
				ToUserAccount:   "wallet_b",
			},
		},
	}, "wallet_a")

	if item.Signature != "sig1" {
		t.Fatalf("unexpected signature: %s", item.Signature)
	}
	if item.Type != "swap" {
		t.Fatalf("unexpected normalized type: %s", item.Type)
	}
	if item.Platform != "jupiter" {
		t.Fatalf("unexpected normalized platform: %s", item.Platform)
	}
	if item.ValueSol != -1 {
		t.Fatalf("expected -1 SOL net value, got %f", item.ValueSol)
	}
	if !item.Success {
		t.Fatal("expected success=true for nil transaction error")
	}
}

func TestShouldHideSpamTx(t *testing.T) {
	t.Parallel()

	if !shouldHideSpamTx(accountTxItem{Type: "nft_airdrop"}) {
		t.Fatal("expected nft_airdrop to be hidden")
	}
	if !shouldHideSpamTx(accountTxItem{Description: "Free spam rewards"}) {
		t.Fatal("expected spam description to be hidden")
	}
	if shouldHideSpamTx(accountTxItem{Type: "swap", Description: "Regular swap"}) {
		t.Fatal("did not expect regular swap to be hidden")
	}
}

