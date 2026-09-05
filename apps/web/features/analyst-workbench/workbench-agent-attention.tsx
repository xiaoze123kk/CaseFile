"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./workbench-agent-attention.module.css";

/** Presentation-only adapter, also covering Leaflet's non-React marker DOM. */
export function AgentAttentionSurface({ ids, children }: { ids: string[]; children: ReactNode }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = root.current;
    if (!element) return;
    const allowed = new Set(ids);
    function paint() {
      element!.querySelectorAll<HTMLElement | SVGElement>("[data-agent-object-id]").forEach((node) => {
        const active = allowed.has(node.getAttribute("data-agent-object-id") ?? "");
        if (active) node.setAttribute("data-agent-focus", "true");
        else node.removeAttribute("data-agent-focus");
      });
    }
    paint();
    const observer = new MutationObserver(paint);
    observer.observe(element, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-agent-object-id"] });
    return () => {
      observer.disconnect();
      element.querySelectorAll("[data-agent-focus]").forEach((node) => node.removeAttribute("data-agent-focus"));
    };
  }, [ids]);
  return <div ref={root} className={styles.surface}>{children}</div>;
}
