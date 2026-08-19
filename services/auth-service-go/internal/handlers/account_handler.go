package handlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
	"github.com/oprai/oprai/services/auth-service-go/internal/services"
)

// AccountHandler serves the account view and identity-linking endpoints — the
// backbone of the multichain profile: one account, many wallets / logins.
type AccountHandler struct {
	queries          *db.Queries
	nonceService     *services.NonceService
	telegramBotToken string
}

// NewAccountHandler creates a new AccountHandler. telegramBotToken may be empty
// (Telegram linking then reports "not configured").
func NewAccountHandler(queries *db.Queries, nonceService *services.NonceService, telegramBotToken string) *AccountHandler {
	return &AccountHandler{queries: queries, nonceService: nonceService, telegramBotToken: telegramBotToken}
}

// resolveAccount returns the caller's account id (users.id) from their
// authenticated wallet, or "" (having already written the error response).
func (h *AccountHandler) resolveAccount(w http.ResponseWriter, r *http.Request) (accountID, wallet string) {
	wallet = middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return "", ""
	}
	// Resolve the account via the SIGNED-IN wallet's identity, not
	// users.wallet_address. The primary (which wallet_address tracks) can point
	// at a DIFFERENT wallet than the one the caller signed in with — e.g. after
	// a set-primary, or when signing in with a secondary wallet — and that must
	// still resolve to the same account.
	for _, typ := range []string{"solana_wallet", "evm_wallet"} {
		if id, err := h.queries.GetIdentityByTypeIdentifier(r.Context(), typ, wallet); err == nil && id != nil {
			return id.AccountID, wallet
		}
	}
	// Legacy fallback (wallet predates linked_identities backfill).
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

	// One Solana wallet per account.
	if other, err := h.queries.HasOtherIdentityOfType(r.Context(), accountID, "solana_wallet", req.WalletAddress); err == nil && other {
		writeError(w, http.StatusConflict, "You already have a Solana wallet linked. Unlink it first to add a different one.")
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
	// Wallets are permanently bound to the account — once a Solana and an EVM
	// wallet are linked they can't be separated (prevents unlink/relink games
	// that would let someone shuffle wallets between accounts to farm rewards).
	// Socials (Telegram, Twitter) stay disconnectable.
	if li.Type == "solana_wallet" || li.Type == "evm_wallet" {
		writeError(w, http.StatusForbidden, "Linked wallets are permanent and can't be unlinked.")
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

// HandleLinkEVMVerify handles POST /account/link/evm/verify — verify an EIP-191
// personal_sign over the link challenge and attach the (proven) EVM wallet.
func (h *AccountHandler) HandleLinkEVMVerify(w http.ResponseWriter, r *http.Request) {
	accountID, _ := h.resolveAccount(w, r)
	if accountID == "" {
		return
	}

	var req linkVerifyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}
	address := services.NormalizeEVMAddress(req.WalletAddress)
	if address == "" || req.Signature == "" || req.NonceID == "" {
		writeError(w, http.StatusBadRequest, "A valid EVM address, signature and nonceId are required")
		return
	}

	nonce, err := h.nonceService.Consume(r.Context(), req.NonceID)
	if err != nil || nonce == "" {
		writeError(w, http.StatusBadRequest, "Challenge missing or expired")
		return
	}
	message := []byte(fmt.Sprintf("OPRAI link wallet: %s", nonce))
	if !services.VerifyEVMSignature(address, message, req.Signature) {
		writeError(w, http.StatusUnauthorized, "Invalid signature")
		return
	}

	existing, err := h.queries.GetIdentityByTypeIdentifier(r.Context(), "evm_wallet", address)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to check identity")
		return
	}
	if existing != nil {
		if existing.AccountID == accountID {
			h.writeIdentities(w, r, accountID, map[string]any{"alreadyLinked": true})
			return
		}
		writeError(w, http.StatusConflict, "This wallet is already linked to a different OPRAI account")
		return
	}

	// One EVM wallet per account.
	if other, err := h.queries.HasOtherIdentityOfType(r.Context(), accountID, "evm_wallet", address); err == nil && other {
		writeError(w, http.StatusConflict, "You already have an Ethereum wallet linked. Unlink it first to add a different one.")
		return
	}

	if _, err := h.queries.InsertIdentity(r.Context(), accountID, "evm_wallet", "ethereum", address, false); err != nil {
		slog.Error("account: insert evm identity failed", "account", accountID, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to link wallet")
		return
	}
	slog.Info("account: evm wallet linked", "account", accountID, "linked", address)
	h.writeIdentities(w, r, accountID, map[string]any{"linked": true})
}

// HandleLinkTelegram handles POST /account/link/telegram — verify a Telegram
// Login Widget payload (HMAC over the bot token) and attach the Telegram id.
func (h *AccountHandler) HandleLinkTelegram(w http.ResponseWriter, r *http.Request) {
	accountID, _ := h.resolveAccount(w, r)
	if accountID == "" {
		return
	}
	if h.telegramBotToken == "" {
		writeError(w, http.StatusServiceUnavailable, "Telegram linking is not configured")
		return
	}

	// Accept the widget fields as a flat string map (id, auth_date, hash, …).
	var raw map[string]any
	if err := json.NewDecoder(r.Body).Decode(&raw); err != nil {
		writeError(w, http.StatusBadRequest, "Invalid request body")
		return
	}
	data := make(map[string]string, len(raw))
	for k, v := range raw {
		data[k] = stringifyField(v)
	}

	tgID, err := services.VerifyTelegramAuth(h.telegramBotToken, data)
	if err != nil {
		writeError(w, http.StatusUnauthorized, err.Error())
		return
	}

	existing, err := h.queries.GetIdentityByTypeIdentifier(r.Context(), "telegram", tgID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "Failed to check identity")
		return
	}
	if existing != nil {
		if existing.AccountID == accountID {
			h.writeIdentities(w, r, accountID, map[string]any{"alreadyLinked": true})
			return
		}
		writeError(w, http.StatusConflict, "This Telegram account is already linked to a different OPRAI account")
		return
	}

	if _, err := h.queries.InsertIdentity(r.Context(), accountID, "telegram", "", tgID, false); err != nil {
		slog.Error("account: insert telegram identity failed", "account", accountID, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to link Telegram")
		return
	}
	slog.Info("account: telegram linked", "account", accountID, "telegram", tgID)
	h.writeIdentities(w, r, accountID, map[string]any{"linked": true})
}

// HandleSetPrimary handles POST /account/identity/{id}/primary — promote a
// Solana-wallet identity to primary (repoints the login/economics key).
func (h *AccountHandler) HandleSetPrimary(w http.ResponseWriter, r *http.Request) {
	accountID, _ := h.resolveAccount(w, r)
	if accountID == "" {
		return
	}
	id := chi.URLParam(r, "id")

	newWallet, err := h.queries.SetPrimaryIdentity(r.Context(), accountID, id)
	if err != nil {
		switch {
		case errors.Is(err, db.ErrNonWalletPrimary):
			writeError(w, http.StatusBadRequest, "Only a wallet can be your primary")
		case errors.Is(err, pgx.ErrNoRows):
			writeError(w, http.StatusNotFound, "Identity not found")
		default:
			slog.Error("account: set primary failed", "account", accountID, "id", id, "error", err)
			writeError(w, http.StatusInternalServerError, "Failed to set primary wallet")
		}
		return
	}
	slog.Info("account: primary changed", "account", accountID, "wallet", newWallet)
	h.writeIdentities(w, r, accountID, map[string]any{"primaryChanged": true, "primaryWallet": newWallet})
}

// stringifyField renders a JSON field as the exact string Telegram signed. The
// widget sends numbers (id, auth_date) that JSON decodes as float64 — format
// them without a decimal point so the HMAC check-string matches.
func stringifyField(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case float64:
		if t == float64(int64(t)) {
			return fmt.Sprintf("%d", int64(t))
		}
		return fmt.Sprintf("%v", t)
	case bool:
		if t {
			return "true"
		}
		return "false"
	default:
		return fmt.Sprintf("%v", t)
	}
}
