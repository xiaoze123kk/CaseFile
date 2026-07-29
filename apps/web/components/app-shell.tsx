"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { ArchiveShell } from "@/components/archive-shell";
import { DemoArchiveShell } from "@/components/demo-archive-shell";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const demoMode = pathname === "/demo" || pathname.startsWith("/demo/");

  return demoMode ? (
    <DemoArchiveShell>{children}</DemoArchiveShell>
  ) : (
    <ArchiveShell>{children}</ArchiveShell>
  );
}
