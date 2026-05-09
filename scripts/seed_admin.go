// seed_admin.go generates a bcrypt hash for the given password.
// Used by scripts/db/seed_admin.sh instead of Python bcrypt.
//
// Usage: go run scripts/seed_admin.go <password>
package main

import (
	"fmt"
	"os"

	"golang.org/x/crypto/bcrypt"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintf(os.Stderr, "Usage: %s <password>\n", os.Args[0])
		os.Exit(1)
	}
	password := os.Args[1]
	if len(password) < 16 {
		fmt.Fprintf(os.Stderr, "ERROR: password must be at least 16 characters\n")
		os.Exit(1)
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), 12)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	fmt.Print(string(hash))
}
