import type { Metadata } from "next";

import { QualityWorkspace } from "@/features/quality/quality-workspace";

export const metadata: Metadata = {
  title: "质量中心",
};

export default function QualityPage() {
  return <QualityWorkspace />;
}
