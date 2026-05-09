package services

import (
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/oprai/oprai/services/admin-service-go/internal/config"
)

// AdminClaims represents the JWT claims for an admin user.
type AdminClaims struct {
	Username string `json:"sub"`
	Role     string `json:"role"`
	jwt.RegisteredClaims
}

// AdminAuth provides admin authentication services (JWT creation and verification).
type AdminAuth struct {
	secret []byte
	ttl    time.Duration
}

// NewAdminAuth creates a new AdminAuth service.
func NewAdminAuth(cfg *config.Config) *AdminAuth {
	return &AdminAuth{
		secret: []byte(cfg.AdminJWTSecret),
		ttl:    time.Duration(cfg.AdminJWTTTL) * time.Second,
	}
}

// CreateToken creates a new JWT token for the given admin user.
// Returns the token string and the expiration time as an ISO 8601 string.
func (a *AdminAuth) CreateToken(username, role string) (string, string, error) {
	now := time.Now()
	expiresAt := now.Add(a.ttl)

	claims := AdminClaims{
		Username: username,
		Role:     role,
		RegisteredClaims: jwt.RegisteredClaims{
			Subject:   username,
			IssuedAt:  jwt.NewNumericDate(now),
			ExpiresAt: jwt.NewNumericDate(expiresAt),
		},
	}

	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	tokenString, err := token.SignedString(a.secret)
	if err != nil {
		return "", "", fmt.Errorf("failed to sign token: %w", err)
	}

	return tokenString, expiresAt.UTC().Format(time.RFC3339), nil
}

// VerifyToken validates a JWT token and returns the extracted claims.
func (a *AdminAuth) VerifyToken(tokenString string) (*AdminClaims, error) {
	token, err := jwt.ParseWithClaims(tokenString, &AdminClaims{}, func(token *jwt.Token) (interface{}, error) {
		if _, ok := token.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("unexpected signing method: %v", token.Header["alg"])
		}
		return a.secret, nil
	})
	if err != nil {
		return nil, fmt.Errorf("invalid token: %w", err)
	}

	claims, ok := token.Claims.(*AdminClaims)
	if !ok || !token.Valid {
		return nil, fmt.Errorf("invalid token claims")
	}

	if claims.Username == "" && claims.Subject != "" {
		claims.Username = claims.Subject
	}

	return claims, nil
}
