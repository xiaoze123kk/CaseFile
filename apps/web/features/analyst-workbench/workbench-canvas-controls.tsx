import styles from "./workbench-canvas.module.css";
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
        data-tooltip="点击切换多选；框选一组节点"
        onClick={() => onToolChange("select")}
        type="button"
      >
        <WorkbenchIcon name="cursor" />
      </button>
      <button
        aria-label="平移工具"
        aria-pressed={tool === "pan"}
        data-tooltip="拖动画布"
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
        data-tooltip="缩小画布"
        disabled={zoom <= 0.5}
        onClick={() => onZoomChange(Math.max(0.5, zoom - 0.25))}
        type="button"
      >
        −
      </button>
      <button
        aria-label={`缩放比例 ${Math.round(zoom * 100)}%`}
        data-tooltip="恢复为 100%"
        onClick={() => onZoomChange(1)}
        type="button"
      >
        {Math.round(zoom * 100)}%
      </button>
      <button
        aria-label="放大"
        data-tooltip="放大画布"
        disabled={zoom >= 2.5}
        onClick={() => onZoomChange(Math.min(2.5, zoom + 0.25))}
        type="button"
      >
        +
      </button>
    </div>
  );
}

export function CanvasKernelControls({
  tool,
  zoom,
  canUndo,
  canRedo,
  isFullscreen,
  onToolChange,
  onFit,
  onToggleFullscreen,
  onZoomOut,
  onResetViewport,
  onZoomIn,
  onRelayout,
  onUndo,
  onRedo,
}: {
  tool: CanvasTool;
  zoom: number;
  canUndo: boolean;
  canRedo: boolean;
  isFullscreen: boolean;
  onToolChange: (tool: CanvasTool) => void;
  onFit: () => void;
  onToggleFullscreen: () => void;
  onZoomOut: () => void;
  onResetViewport: () => void;
  onZoomIn: () => void;
  onRelayout: () => void;
  onUndo: () => void;
  onRedo: () => void;
}) {
  return (
    <>
      <CanvasTools onToolChange={onToolChange} tool={tool} />
      <div aria-label="画布视口" className={styles.canvasActionTools}>
        <button
          aria-label="适配全部"
          data-tooltip="适配全部节点"
          onClick={onFit}
          type="button"
        >
          适配
        </button>
        <button
          aria-label={isFullscreen ? "退出全屏" : "全屏查看画布"}
          data-tooltip={isFullscreen ? "退出全屏" : "全屏查看画布"}
          onClick={onToggleFullscreen}
          type="button"
        >
          ⛶
        </button>
        <button
          aria-label="重新整理"
          data-tooltip="重新整理布局"
          onClick={onRelayout}
          type="button"
        >
          ↻
        </button>
      </div>
      <div aria-label="布局历史" className={styles.canvasActionTools}>
        <button
          aria-label="撤销布局修改"
          data-tooltip="撤销布局修改"
          disabled={!canUndo}
          onClick={onUndo}
          type="button"
        >
          ↶
        </button>
        <button
          aria-label="重做布局修改"
          data-tooltip="重做布局修改"
          disabled={!canRedo}
          onClick={onRedo}
          type="button"
        >
          ↷
        </button>
      </div>
      <div aria-label="画布缩放" className={styles.zoomControls}>
        <button
          aria-label="缩小"
          data-tooltip="缩小画布"
          disabled={zoom <= 0.12}
          onClick={onZoomOut}
          type="button"
        >
          −
        </button>
        <button
          aria-label={`缩放比例 ${Math.round(zoom * 100)}%`}
          data-tooltip="恢复为 100%"
          onClick={onResetViewport}
          type="button"
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          aria-label="放大"
          data-tooltip="放大画布"
          disabled={zoom >= 2.5}
          onClick={onZoomIn}
          type="button"
        >
          +
        </button>
      </div>
    </>
  );
}
