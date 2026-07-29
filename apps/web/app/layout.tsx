import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { WorkflowProvider } from "@/store/workflow-store";
import { PrototypeProvider } from "@/store/prototype-store";

import { QueryProvider } from "./providers";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "CaseFile 推理卷宗",
    template: "%s · CaseFile",
  },
  description: "CaseFile 本地优先的 AI 推理卷宗工作台",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <QueryProvider>
          <WorkflowProvider>
            <PrototypeProvider>
              <AppShell>{children}</AppShell>
            </PrototypeProvider>
          </WorkflowProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
