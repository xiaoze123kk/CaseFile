import styles from "./analyst-workbench.module.css";
import { WorkbenchIcon } from "./workbench-icon";

export type CanvasTool = "select" | "pan";

export function CanvasTools({
  tool,
  onToolChange,
}: {
  tool: CanvasTool;
  onToolChange: (tool: CanvasTool) => void;
}) {
  return (
    <div aria-label="画布工具" className={styles.canvasTools}>
      <button
        aria-label="选择工具"
        aria-pressed={tool === "select"}
        onClick={() => onToolChange("select")}
        type="button"
      >
        <WorkbenchIcon name="cursor" />
      </button>
      <button
        aria-label="平移工具"
        aria-pressed={tool === "pan"}
        onClick={() => onToolChange("pan")}
        type="button"
      >
        <WorkbenchIcon name="hand" />
      </button>
    </div>
  );
}

export function ZoomControls({
  zoom,
  onZoomChange,
}: {
  zoom: number;
  onZoomChange: (zoom: number) => void;
}) {
  return (
    <div aria-label="画布缩放" className={styles.zoomControls}>
      <button
        aria-label="缩小"
        disabled={zoom <= 0.5}
        onClick={() => onZoomChange(Math.max(0.5, zoom - 0.25))}
        type="button"
      >
        −
      </button>
      <button
        aria-label={`缩放比例 ${Math.round(zoom * 100)}%`}
        onClick={() => onZoomChange(1)}
        type="button"
      >
        {Math.round(zoom * 100)}%
      </button>
      <button
        aria-label="放大"
        disabled={zoom >= 2.5}
        onClick={() => onZoomChange(Math.min(2.5, zoom + 0.25))}
        type="button"
      >
        +
      </button>
    </div>
  );
}
