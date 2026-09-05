"use client";

import { useLayoutEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Keep React and the textarea DOM identity stable when changing docking slots. */
export function WorkbenchAgentPortal({ host, children }: {
  host?: HTMLElement | null;
  children: ReactNode;
}) {
  const [container] = useState(() => {
    if (typeof document === "undefined") return null;
    const element = document.createElement("div");
    element.style.height = "100%";
    element.style.minHeight = "0";
    return element;
  });
  const [composing, setComposing] = useState(false);
  useLayoutEffect(() => {
    if (!host || !container || composing || container.parentElement === host) return;
    const active = document.activeElement;
    const focused = active instanceof HTMLElement && container.contains(active);
    const selection = active instanceof HTMLTextAreaElement
      ? [active.selectionStart, active.selectionEnd] as const : null;
    // State-preserving moves are available in modern desktop browsers.
    const destination = host as HTMLElement & { moveBefore?: (node: Node, before: Node | null) => void };
    if (destination.moveBefore && container.isConnected) destination.moveBefore(container, null);
    else host.appendChild(container);
    if (focused) {
      active.focus({ preventScroll: true });
      if (selection && active instanceof HTMLTextAreaElement) active.setSelectionRange(...selection);
    }
  }, [composing, container, host]);
  useLayoutEffect(() => () => container?.remove(), [container]);
  const content = <div style={{ height: "100%", minHeight: 0 }}
    onCompositionStartCapture={() => setComposing(true)}
    onCompositionEndCapture={() => setComposing(false)}>{children}</div>;
  return container && host ? createPortal(content, container) : content;
}
