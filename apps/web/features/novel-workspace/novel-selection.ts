/** DOM selection offsets are UTF-16; the server protocol uses Unicode code points. */
export const codePointOffset = (text: string, offset: number) =>
  Array.from(text.slice(0, offset)).length;
export const utf16Offset = (text: string, offset: number) =>
  Array.from(text).slice(0, offset).join("").length;
export function selectionMenuPosition(target: HTMLTextAreaElement) {
  const box = target.getBoundingClientRect(),
    style = getComputedStyle(target),
    mirror = document.createElement("div");
  for (const property of Array.from(style))
    mirror.style.setProperty(property, style.getPropertyValue(property));
  Object.assign(mirror.style, {
    position: "fixed",
    left: `${box.left}px`,
    top: `${box.top - target.scrollTop}px`,
    width: `${box.width}px`,
    height: "auto",
    minHeight: "0",
    visibility: "hidden",
    whiteSpace: "pre-wrap",
    overflowWrap: "break-word",
    pointerEvents: "none",
  });
  mirror.textContent = target.value.slice(0, target.selectionStart);
  const marker = document.createElement("span");
  marker.textContent =
    target.value.slice(target.selectionStart, target.selectionEnd) || " ";
  mirror.append(marker);
  document.body.append(mirror);
  const rect = marker.getBoundingClientRect();
  mirror.remove();
  return {
    left: Math.max(8, Math.min(rect.left, window.innerWidth - 250)),
    top: Math.max(8, Math.min(rect.top - 45, window.innerHeight - 55)),
  };
}
