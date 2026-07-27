import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ArchiveShell } from "@/components/archive-shell";
import { PrototypeProvider } from "@/store/prototype-store";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "CaseFile 推理卷宗",
    template: "%s · CaseFile",
  },
  description: "CaseFile 本地可点击前端原型",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <PrototypeProvider>
          <ArchiveShell>{children}</ArchiveShell>
        </PrototypeProvider>
      </body>
    </html>
  );
}
