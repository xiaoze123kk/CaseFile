"use client";

import { usePathname } from "next/navigation";
import { type ReactNode, useState } from "react";

import { DemoPrototypeProvider } from "@/features/demo-prototype/demo-prototype-provider";
import { SettingsDialog } from "@/features/workflow/settings-dialog";

import styles from "./demo-settings-entry.module.css";

export function DemoArchiveShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [settingsOpen, setSettingsOpen] = useState(false);

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
      <button
        aria-label="打开模型服务设置"
        className={styles.settingsEntry}
        data-demo-surface={intakeMode ? "intake" : "workbench"}
        onClick={() => setSettingsOpen(true)}
        type="button"
      >
        <span aria-hidden="true" className={styles.settingsDot} />
        <span>
          <strong>模型服务</strong>
          <small>密钥设置</small>
        </span>
      </button>
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </DemoPrototypeProvider>
  );
}
