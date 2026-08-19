package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

// EVM NFTs via Alchemy's NFT Data API (multichain, one call). Spam collections
// are dropped.

type alchemyNftResponse struct {
	Data struct {
		OwnedNfts []struct {
			TokenID   string `json:"tokenId"`
			Network   string `json:"network"`
			Name      string `json:"name"`
			TokenType string `json:"tokenType"`
			Image     struct {
				CachedURL    string `json:"cachedUrl"`
				ThumbnailURL string `json:"thumbnailUrl"`
				PngURL       string `json:"pngUrl"`
				OriginalURL  string `json:"originalUrl"`
			} `json:"image"`
			Contract struct {
				Address         string `json:"address"`
				Name            string `json:"name"`
				IsSpam          bool   `json:"isSpam"`
				OpenSeaMetadata struct {
					ImageURL       string `json:"imageUrl"`
					CollectionName string `json:"collectionName"`
				} `json:"openSeaMetadata"`
			} `json:"contract"`
		} `json:"ownedNfts"`
	} `json:"data"`
}

type evmNft struct {
	Chain      string `json:"chain"`
	Name       string `json:"name"`
	Collection string `json:"collection"`
	Image      string `json:"image"`
	TokenID    string `json:"tokenId"`
}

// GetEvmNfts handles GET /market/evm/nfts?address=0x.. — the wallet's NFTs across
// the major EVM chains (spam dropped).
func (m *MarketProxy) GetEvmNfts(w http.ResponseWriter, r *http.Request) {
	if m.alchemyAPIKey == "" {
		writeError(w, http.StatusServiceUnavailable, "EVM NFTs are not configured")
		return
	}
	address := strings.TrimSpace(r.URL.Query().Get("address"))
	if !looksLikeEVMAddress(address) {
		writeError(w, http.StatusBadRequest, "A valid 0x EVM address is required")
		return
	}
	address = strings.ToLower(address)

	cacheKey := "evm-nfts:" + address
	if cached, ok := m.cache.Get(cacheKey); ok {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		w.Write(cached)
		return
	}

	networkIDs := make([]string, 0, len(evmNetworks))
	for _, n := range evmNetworks {
		networkIDs = append(networkIDs, n.id)
	}
	reqBody, _ := json.Marshal(map[string]any{
		"addresses":    []map[string]any{{"address": address, "networks": networkIDs}},
		"withMetadata": true,
		"pageSize":     100,
	})

	url := fmt.Sprintf("https://api.g.alchemy.com/data/v1/%s/assets/nfts/by-address", m.alchemyAPIKey)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, url, bytes.NewReader(reqBody))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to build request")
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	resp, err := m.client.Do(req)
	if err != nil {
		slog.Error("alchemy nfts error", "error", err)
		writeError(w, http.StatusBadGateway, "NFT provider unavailable")
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		writeError(w, http.StatusBadGateway, "NFT provider error")
		return
	}

	var parsed alchemyNftResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		writeError(w, http.StatusBadGateway, "NFT decode failed")
		return
	}

	nfts := make([]evmNft, 0, len(parsed.Data.OwnedNfts))
	for _, n := range parsed.Data.OwnedNfts {
		if n.Contract.IsSpam {
			continue
		}
		img := firstNonEmpty(n.Image.CachedURL, n.Image.ThumbnailURL, n.Image.PngURL, n.Image.OriginalURL, n.Contract.OpenSeaMetadata.ImageURL)
		if img == "" {
			continue // no image → skip (avoids empty grey tiles)
		}
		coll := firstNonEmpty(n.Contract.OpenSeaMetadata.CollectionName, n.Contract.Name)
		name := firstNonEmpty(n.Name, coll+" #"+n.TokenID)
		nfts = append(nfts, evmNft{
			Chain: networkMeta(n.Network).chain, Name: name, Collection: coll, Image: img, TokenID: n.TokenID,
		})
	}

	out := map[string]any{"address": address, "nfts": nfts}
	body, err := json.Marshal(out)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to encode NFTs")
		return
	}
	m.cache.Set(cacheKey, body, 60*time.Second)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}
