"use client";

import { usePathname } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

import { CaseSessionProvider } from "@/features/case-session/case-session-provider";
import { SettingsDialog } from "@/features/settings/settings-dialog";
import { LOCAL_ACTOR_ID } from "@/lib/local-session";

export function ProductShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    const openSettings = () => setSettingsOpen(true);
    window.addEventListener("casefile:open-settings", openSettings);
    return () =>
      window.removeEventListener("casefile:open-settings", openSettings);
  }, []);

  const intakeMode = pathname === "/";
  const visualIntakeMode = pathname === "/visual-intake";

  const shellKind = visualIntakeMode
    ? "intake-visual-demo"
    : intakeMode
      ? "intake-center-v1"
      : "analyst-workbench-v1";
  const shellVisual = visualIntakeMode
    ? "living-dossier-spine"
    : intakeMode
      ? "digital-dossier"
      : "graphite-paper-copper";

  return (
    <CaseSessionProvider>
      <div
        data-casefile-kind={shellKind}
        data-casefile-visual={shellVisual}
      >
        {children}
      </div>
      <SettingsDialog
        actorId={LOCAL_ACTOR_ID}
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </CaseSessionProvider>
  );
}
