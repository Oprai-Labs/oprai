package handlers

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

// AccountHandler serves the account view and identity-linking endpoints — the
// backbone of the multichain profile: one account, many wallets / logins.
type AccountHandler struct {
	queries      *db.Queries
	nonceService *services.NonceService
}

// NewAccountHandler creates a new AccountHandler.
func NewAccountHandler(queries *db.Queries, nonceService *services.NonceService) *AccountHandler {
	return &AccountHandler{queries: queries, nonceService: nonceService}
}

// resolveAccount returns the caller's account id (users.id) from their
// authenticated wallet, or "" (having already written the error response).
func (h *AccountHandler) resolveAccount(w http.ResponseWriter, r *http.Request) (accountID, wallet string) {
	wallet = middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return "", ""
	}
	user, err := h.queries.GetUserByWallet(r.Context(), wallet)
	if err != nil {
		slog.Error("account: get user by wallet failed", "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to load account")
		return "", ""
	}
	if user == nil {
		writeError(w, http.StatusNotFound, "Account not found")
		return "", ""
	}
	return user.ID, wallet
}

func (h *AccountHandler) writeIdentities(w http.ResponseWriter, r *http.Request, accountID string, extra map[string]any) {
	identities, err := h.queries.ListIdentitiesByAccount(r.Context(), accountID)
	if err != nil {
		slog.Error("account: list identities failed", "account", accountID, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to load identities")
		return
	}
	out := make([]map[string]any, 0, len(identities))
	for i := range identities {
		out = append(out, identities[i].ToJSON())
	}
	resp := map[string]any{"accountId": accountID, "identities": out}
	for k, v := range extra {
		resp[k] = v
	}
	writeJSON(w, http.StatusOK, resp)
}

// HandleGetMe handles GET /account/me — the caller's account and its identities.
func (h *AccountHandler) HandleGetMe(w http.ResponseWriter, r *http.Request) {
	accountID, _ := h.resolveAccount(w, r)
	if accountID == "" {
		return
	}
	h.writeIdentities(w, r, accountID, nil)
}

// HandleLinkNonce handles POST /account/link/nonce — issue a challenge the user
// signs with the wallet they want to add, proving control before it is linked.
func (h *AccountHandler) HandleLinkNonce(w http.ResponseWriter, r *http.Request) {
	if accountID, _ := h.resolveAccount(w, r); accountID == "" {
		return
	}
	result, err := h.nonceService.Generate(r.Context())
	if err != nil {
		slog.Error("account: link nonce generate failed", "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to issue challenge")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"nonce": result.Nonce, "nonceId": result.NonceID})
}

type linkVerifyRequest struct {
	WalletAddress string `json:"walletAddress"`
	Signature     string `json:"signature"`
	NonceID       string `json:"nonceId"`
}

// HandleLinkVerify handles POST /account/link/verify — verify the signature and
// attach the (proven) Solana wallet to the caller's account.
func (h *AccountHandler) HandleLinkVerify(w http.ResponseWriter, r *http.Request) {
	accountID, primaryWallet := h.resolveAccount(w, r)
	if accountID == "" {
		return
	}

	var req linkVerifyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}
	if req.WalletAddress == "" || req.Signature == "" || req.NonceID == "" {
		writeError(w, http.StatusBadRequest, "walletAddress, signature and nonceId are required")
		return
	}

	// Consume the challenge (single-use) and verify the signature over a message
	// distinct from login, so a login signature can never be replayed as a link.
	nonce, err := h.nonceService.Consume(r.Context(), req.NonceID)
	if err != nil || nonce == "" {
		writeError(w, http.StatusBadRequest, "Challenge missing or expired")
		return
	}
	message := []byte(fmt.Sprintf("OPRAI link wallet: %s", nonce))
	if !services.VerifySignature(req.WalletAddress, message, req.Signature) {
		writeError(w, http.StatusUnauthorized, "Invalid signature")
		return
	}

	// Is this wallet already linked anywhere?
	existing, err := h.queries.GetIdentityByTypeIdentifier(r.Context(), "solana_wallet", req.WalletAddress)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to check identity")
		return
	}
	if existing != nil {
		if existing.AccountID == accountID {
			// Already yours (e.g. the primary wallet) — idempotent success.
			h.writeIdentities(w, r, accountID, map[string]any{"alreadyLinked": true})
			return
		}
		writeError(w, http.StatusConflict, "This wallet is already linked to a different OPRAI account")
		return
	}

	if _, err := h.queries.InsertIdentity(r.Context(), accountID, "solana_wallet", "solana", req.WalletAddress, false); err != nil {
		slog.Error("account: insert identity failed", "account", accountID, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to link wallet")
		return
	}
	slog.Info("account: wallet linked", "account", accountID, "primary", primaryWallet, "linked", req.WalletAddress)
	h.writeIdentities(w, r, accountID, map[string]any{"linked": true})
}

// HandleUnlink handles DELETE /account/identity/{id} — detach a linked identity.
// The primary login identity is protected (a user can't orphan the wallet the
// whole system keys on); change your primary first once that flow ships.
func (h *AccountHandler) HandleUnlink(w http.ResponseWriter, r *http.Request) {
	accountID, _ := h.resolveAccount(w, r)
	if accountID == "" {
		return
	}
	id := chi.URLParam(r, "id")

	li, err := h.queries.GetIdentityByID(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to load identity")
		return
	}
	if li == nil || li.AccountID != accountID {
		writeError(w, http.StatusNotFound, "Identity not found")
		return
	}
	if li.IsPrimary {
		writeError(w, http.StatusBadRequest, "You can't remove your primary identity")
		return
	}

	n, err := h.queries.DeleteIdentityByID(r.Context(), id, accountID)
	if err != nil {
		slog.Error("account: unlink failed", "account", accountID, "id", id, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to remove identity")
		return
	}
	if n == 0 {
		writeError(w, http.StatusNotFound, "Identity not found")
		return
	}
	slog.Info("account: identity unlinked", "account", accountID, "type", li.Type)
	h.writeIdentities(w, r, accountID, map[string]any{"unlinked": true})
}
