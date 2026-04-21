"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { adminVerify } from "@/lib/admin-api";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    // Verify session via HttpOnly cookie — replaces synchronous localStorage check.
    adminVerify()
      .then(() => router.replace("/dashboard"))
      .catch(() => router.replace("/login"));
  }, [router]);

  return null;
}
