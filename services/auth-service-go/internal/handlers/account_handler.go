package handlers

import (
	"log/slog"
	"net/http"

	"github.com/oprai/oprai/services/auth-service-go/internal/db"
	"github.com/oprai/oprai/services/auth-service-go/internal/middleware"
)

// AccountHandler serves the account view — the account (users row) plus all of
// its linked identities. This is the backbone of the multichain profile: one
// account, many wallets / logins.
type AccountHandler struct {
	queries *db.Queries
}

// NewAccountHandler creates a new AccountHandler.
func NewAccountHandler(queries *db.Queries) *AccountHandler {
	return &AccountHandler{queries: queries}
}

// HandleGetMe handles GET /account/me — the caller's account and its linked
// identities. The account id is resolved from the caller's wallet (which, as an
// account's primary identity, maps 1:1 to users.id), so it works for tokens
// issued before the account_id claim existed.
func (h *AccountHandler) HandleGetMe(w http.ResponseWriter, r *http.Request) {
	wallet := middleware.WalletFromContext(r.Context())
	if wallet == "" {
		writeError(w, http.StatusUnauthorized, "Unauthorized")
		return
	}

	user, err := h.queries.GetUserByWallet(r.Context(), wallet)
	if err != nil {
		slog.Error("account: get user by wallet failed", "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to load account")
		return
	}
	if user == nil {
		writeError(w, http.StatusNotFound, "Account not found")
		return
	}

	identities, err := h.queries.ListIdentitiesByAccount(r.Context(), user.ID)
	if err != nil {
		slog.Error("account: list identities failed", "account", user.ID, "error", err)
		writeError(w, http.StatusInternalServerError, "Failed to load identities")
		return
	}

	out := make([]map[string]any, 0, len(identities))
	for i := range identities {
		out = append(out, identities[i].ToJSON())
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"accountId":  user.ID,
		"identities": out,
	})
}
