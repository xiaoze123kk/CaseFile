import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ProductShell } from "@/components/product-shell";

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
          <ProductShell>{children}</ProductShell>
        </QueryProvider>
      </body>
    </html>
  );
}
