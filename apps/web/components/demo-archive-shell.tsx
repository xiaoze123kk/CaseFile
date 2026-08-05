"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { DemoPrototypeProvider } from "@/features/demo-prototype/demo-prototype-provider";

export function DemoArchiveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  const intakeMode =
    pathname === "/demo/intake" || pathname.startsWith("/demo/intake/");

  return (
    <DemoPrototypeProvider>
      <div
        data-demo-kind={
          intakeMode ? "intake-center-v1" : "analyst-workbench-v1"
        }
        data-demo-visual={
          intakeMode ? "digital-dossier" : "graphite-paper-copper"
        }
      >
        {children}
      </div>
    </DemoPrototypeProvider>
  );
}
