import "@testing-library/jest-dom/vitest";

class ResizeObserverMock implements ResizeObserver {
  disconnect() {}
  observe() {}
  unobserve() {}
}

globalThis.ResizeObserver = ResizeObserverMock;

// jsdom 不实现 SVG getBBox；React Flow 的边标签测量依赖它。
(
  SVGElement.prototype as unknown as { getBBox?: () => DOMRect }
).getBBox ??= () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
