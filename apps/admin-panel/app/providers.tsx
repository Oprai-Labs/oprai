"use client";

import { useState, useEffect, useCallback, type ReactNode } from "react";
import { AdminAuthContext } from "@/hooks/use-admin-auth";
import { adminLogin, adminLogout, adminVerify } from "@/lib/admin-api";

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState<string | null>(null);

  useEffect(() => {
    // Verify session via HttpOnly cookie on mount — replaces localStorage check.
    adminVerify()
      .then((res) => {
        setIsAuthenticated(true);
        setUsername(res.username);
      })
      .catch(() => {
        // Cookie absent or expired — user needs to log in.
        setIsAuthenticated(false);
      });
  }, []);

  const login = useCallback(async (user: string, password: string) => {
    const res = await adminLogin(user, password);
    setIsAuthenticated(true);
    setUsername(res.username);
  }, []);

  const logout = useCallback(async () => {
    await adminLogout(); // clears HttpOnly cookie on the backend
    setIsAuthenticated(false);
    setUsername(null);
    window.location.href = "/login";
  }, []);

  return (
    <AdminAuthContext.Provider value={{ isAuthenticated, username, login, logout }}>
      {children}
    </AdminAuthContext.Provider>
  );
}
