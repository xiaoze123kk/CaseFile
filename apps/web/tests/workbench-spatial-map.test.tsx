import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SpatialMapView } from "@/features/analyst-workbench/spatial-map/spatial-map-view";
import {
  createSpatialRenderer,
  type SpatialRenderer,
} from "@/features/analyst-workbench/spatial-map/spatial-renderer";
import type {
  WorkbenchMapModel,
  WorkbenchSpatialLocation,
} from "@/features/analyst-workbench/workbench-real-data-types";

vi.mock(
  "@/features/analyst-workbench/spatial-map/spatial-renderer",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("@/features/analyst-workbench/spatial-map/spatial-renderer")
      >();
    return { ...actual, createSpatialRenderer: vi.fn() };
  },
);

const geographicLocation: WorkbenchSpatialLocation = {
  spatialId: "loc_geo",
  locationId: "loc_geo",
  label: "地理地点",
  source: "wgs84",
  position: { kind: "wgs84", latitude: 31.23, longitude: 121.47 },
  events: [
    {
      eventId: "evt_geo",
      label: "地理事件",
      time: "第 2 日 09:30",
      relatedObjectIds: ["loc_geo", "ent_a"],
    },
  ],
  relatedObjectIds: ["ent_a"],
};

const sceneLocation: WorkbenchSpatialLocation = {
  spatialId: "loc_scene",
  locationId: "loc_scene",
  label: "场景地点",
  source: "schematic",
  position: { kind: "planar", x: 30, y: 70 },
  events: [],
  relatedObjectIds: [],
};

const inferredLocation: WorkbenchSpatialLocation = {
  spatialId: "loc_inferred",
  locationId: "loc_inferred",
  label: "推算地点",
  source: "inferred",
  position: { kind: "planar", x: 55, y: 45 },
  events: [],
  relatedObjectIds: [],
};

function mapModel(): WorkbenchMapModel {
  return {
    availableModes: ["geographic", "scene"],
    defaultMode: "geographic",
    views: {
      geographic: {
        mode: "geographic",
        locations: [geographicLocation],
        relations: [],
      },
      scene: { mode: "scene", locations: [sceneLocation], relations: [] },
      topology: { mode: "topology", locations: [], relations: [] },
    },
    unlocatedLocationIds: ["loc_missing"],
    unlocatedLocations: [{ locationId: "loc_missing", label: "未定位地点" }],
    counts: {
      locations: 3,
      events: 1,
      geographic: 1,
      scene: 1,
      inferred: 0,
      unlocated: 1,
    },
  };
}

function topologyMapModel(): WorkbenchMapModel {
  return {
    availableModes: ["topology"],
    defaultMode: "topology",
    views: {
      geographic: { mode: "geographic", locations: [], relations: [] },
      scene: { mode: "scene", locations: [], relations: [] },
      topology: {
        mode: "topology",
        locations: [inferredLocation],
        relations: [],
      },
    },
    unlocatedLocationIds: [],
    unlocatedLocations: [],
    counts: {
      locations: 1,
      events: 0,
      geographic: 0,
      scene: 0,
      inferred: 1,
      unlocated: 0,
    },
  };
}

function renderMap(
  overrides: Partial<React.ComponentProps<typeof SpatialMapView>> = {},
) {
  const props: React.ComponentProps<typeof SpatialMapView> = {
    map: mapModel(),
    title: "测试空间卷宗",
    meta: "真实地图 / 场景图",
    note: "1 个地理坐标 · 1 个场景坐标",
    selectedObjectId: null,
    selectedEventId: null,
    onSelectLocation: vi.fn(() => true),
    onSelectEvent: vi.fn(() => true),
    onClearSelection: vi.fn(() => true),
    onOpenLocationDetails: vi.fn(() => true),
    ...overrides,
  };
  return { ...render(<SpatialMapView {...props} />), props };
}

const renderers: Array<{
  mode: string;
  renderer: SpatialRenderer;
}> = [];
let reportTileError = false;

beforeEach(() => {
  reportTileError = false;
  renderers.length = 0;
  delete process.env.NEXT_PUBLIC_CASEFILE_MAP_TILE_URL;
  delete process.env.NEXT_PUBLIC_CASEFILE_MAP_ATTRIBUTION;
  vi.mocked(createSpatialRenderer).mockImplementation(({ container, mode }) => {
    let currentViewport = { center: [50, 50] as [number, number], zoom: 1 };
    const renderer: SpatialRenderer = {
      render(view, selection, callbacks, options) {
        container.replaceChildren();
        for (const location of view.locations) {
          const marker = document.createElement("button");
          marker.type = "button";
          marker.textContent = location.label;
          marker.setAttribute(
            "aria-label",
            `${location.label} marker${options.editableLocationId === location.locationId ? " editable" : ""}`,
          );
          marker.setAttribute(
            "aria-pressed",
            String(
              selection.activeSpatialId === location.spatialId ||
                selection.selectedLocationId === location.locationId,
            ),
          );
          marker.addEventListener("click", () => callbacks.onActivateLocation(location));
          marker.addEventListener("keydown", (event) => {
            if (
              event.key === "ArrowRight" &&
              options.editableLocationId === location.locationId
            ) {
              callbacks.onPreviewPosition(
                location,
                location.position.kind === "wgs84"
                  ? {
                      ...location.position,
                      longitude: location.position.longitude + 0.0001,
                    }
                  : { ...location.position, x: location.position.x + 0.5 },
              );
              return;
            }
            if (event.key === "Enter" || event.key === " ") {
              callbacks.onActivateLocation(location);
            }
          });
          container.append(marker);
        }
        if (reportTileError && mode === "geographic") callbacks.onTileError();
      },
      fitAll: vi.fn(),
      focusLocation: vi.fn(),
      zoomIn: vi.fn(),
      zoomOut: vi.fn(),
      getViewport: vi.fn(() => currentViewport),
      setViewport: vi.fn((viewport) => {
        currentViewport = viewport;
      }),
      invalidateSize: vi.fn(),
      destroy: vi.fn(),
    };
    renderers.push({ mode, renderer });
    return renderer;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  delete process.env.NEXT_PUBLIC_CASEFILE_MAP_TILE_URL;
  delete process.env.NEXT_PUBLIC_CASEFILE_MAP_ATTRIBUTION;
});

describe("spatial map view", () => {
  it("keeps Leaflet behind a client-only dynamic boundary with a loading state", () => {
    const source = readFileSync(
      resolve(process.cwd(), "features/analyst-workbench/analyst-workbench.tsx"),
      "utf8",
    );

    expect(source).toContain("ssr: false");
    expect(source).toContain("正在载入空间卷宗");
    expect(source).toContain('import("./spatial-map/spatial-map-view")');
  });

  it("switches CRS modes by destroying and rebuilding the renderer", async () => {
    renderMap();

    await screen.findByRole("button", { name: "地理地点 marker" });
    const firstRenderer = renderers[0].renderer;
    fireEvent.click(screen.getByRole("button", { name: "场景图" }));

    await screen.findByRole("button", { name: "场景地点 marker" });
    expect(firstRenderer.destroy).toHaveBeenCalledOnce();
    expect(renderers.map((entry) => entry.mode)).toEqual(["geographic", "scene"]);

    fireEvent.click(screen.getByRole("button", { name: "真实地图" }));
    await screen.findByRole("button", { name: "地理地点 marker" });
    expect(renderers[1].renderer.destroy).toHaveBeenCalledOnce();
    expect(renderers[2].renderer.setViewport).toHaveBeenCalledWith({
      center: [50, 50],
      zoom: 1,
    });
  });

  it("supports keyboard markers and event selection inside the location preview", async () => {
    const onSelectLocation = vi.fn(() => true);
    const onSelectEvent = vi.fn(() => true);
    renderMap({ onSelectLocation, onSelectEvent });
    const marker = await screen.findByRole("button", { name: "地理地点 marker" });

    fireEvent.keyDown(marker, { key: "Enter" });

    expect(onSelectLocation).toHaveBeenCalledWith("loc_geo");
    expect(await screen.findByRole("dialog")).toHaveTextContent("WGS84 地理坐标");
    const eventButton = screen.getByRole("button", { name: /第 2 日 09:30地理事件/u });
    fireEvent.keyDown(eventButton, { key: "Enter" });
    expect(onSelectEvent).toHaveBeenCalledWith("evt_geo");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("focuses a selected object in its available mode without mixing mode data", async () => {
    renderMap({ selectedObjectId: "loc_scene" });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "场景图" })).toHaveAttribute(
        "aria-pressed",
        "true",
      ),
    );
    expect(screen.getByRole("button", { name: "场景地点 marker" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "地理地点 marker" })).toBeNull();
  });

  it("shows an honest empty state and an unlocated count", () => {
    const empty = mapModel();
    empty.availableModes = [];
    empty.defaultMode = null;
    empty.views.geographic.locations = [];
    empty.views.scene.locations = [];
    renderMap({ map: empty });

    expect(screen.getByText("当前工作稿没有可呈现的位置")).toBeInTheDocument();
    expect(screen.getByText("1 个地点未定位")).toBeInTheDocument();
    expect(createSpatialRenderer).not.toHaveBeenCalled();
  });

  it("rejects partial tile configuration without hiding geographic markers", async () => {
    process.env.NEXT_PUBLIC_CASEFILE_MAP_TILE_URL = "https://tiles.test/{z}/{x}/{y}.png";
    renderMap();

    expect(
      await screen.findByText(/地图瓦片配置不完整/u),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "地理地点 marker" })).toBeInTheDocument();
  });

  it("keeps coordinate markers visible when the tile provider fails", async () => {
    reportTileError = true;
    renderMap();

    expect(await screen.findByText(/底图暂不可用/u)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "地理地点 marker" })).toBeInTheDocument();
  });

  it("controls four audit layers while keeping status counts independent", async () => {
    const model = mapModel();
    const secondLocation: WorkbenchSpatialLocation = {
      ...geographicLocation,
      spatialId: "loc_geo_b",
      locationId: "loc_geo_b",
      label: "第二地点",
      position: { kind: "wgs84", latitude: 31.24, longitude: 121.49 },
      events: [],
    };
    model.views.geographic.locations.push(secondLocation);
    model.counts.geographic = 2;
    model.counts.locations = 4;
    model.views.geographic.relations.push({
      relationId: "adjacency:loc_geo:loc_geo_b",
      kind: "adjacency",
      fromLocationId: "loc_geo",
      toLocationId: "loc_geo_b",
      direction: "undirected",
      label: "相邻",
      minutes: null,
    });
    renderMap({ map: model });

    expect(screen.getByRole("checkbox", { name: /地点明确坐标/u })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /事件地点聚合/u })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /空间关系只读核对/u })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /待确认位置推算与未定位/u })).not.toBeChecked();
    expect(screen.getByText("真实坐标").parentElement).toHaveTextContent("2");

    fireEvent.click(screen.getByRole("checkbox", { name: /空间关系只读核对/u }));
    expect(screen.getByText("关系连线不代表实际路线。")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "当前可见空间关系" })).toHaveTextContent(
      "loc_geo",
    );

    fireEvent.click(screen.getByRole("button", { name: "地理地点 marker" }));
    expect(await screen.findByRole("button", { name: /地理事件/u })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /事件地点聚合/u }));
    expect(screen.queryByRole("button", { name: /地理事件/u })).toBeNull();
    expect(screen.getByText(/事件图层已关闭/u)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /地点明确坐标/u }));
    expect(screen.queryByRole("button", { name: "地理地点 marker" })).toBeNull();
    expect(screen.getByText(/当前图层组合没有可见地点/u)).toBeInTheDocument();
    expect(screen.getByText("真实坐标").parentElement).toHaveTextContent("2");
  });

  it("opens an unlocated location in the existing object inspector entry", () => {
    const onOpenLocationDetails = vi.fn(() => true);
    renderMap({ onOpenLocationDetails });

    fireEvent.click(screen.getByRole("checkbox", { name: /待确认位置推算与未定位/u }));
    fireEvent.click(screen.getByRole("button", { name: /未定位地点loc_missing/u }));

    expect(onOpenLocationDetails).toHaveBeenCalledWith("loc_missing");
  });

  it("collapses and reopens the coordinate audit panel", () => {
    renderMap();

    const toggle = screen.getByRole("button", { name: "空间核验" });
    const panel = screen.getByRole("complementary", { name: "空间核验工具" });
    expect(panel).toHaveAttribute("data-collapsed", "false");

    fireEvent.click(screen.getByRole("button", { name: "收起空间核验工具" }));
    expect(panel).toHaveAttribute("data-collapsed", "true");
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(panel).toHaveAttribute("data-collapsed", "false");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("closes the mobile audit drawer from the keyboard", () => {
    renderMap();

    const toggle = screen.getByRole("button", { name: "空间核验" });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(screen.getByRole("button", { name: "收起空间核验工具" }), {
      key: "Enter",
    });

    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps drag and keyboard movement local until explicit save", async () => {
    const onSaveSpatialPosition = vi.fn(async () => "saved" as const);
    const onPositionEditStateChange = vi.fn();
    renderMap({
      onPositionEditStateChange,
      onReloadSpatialLocation: vi.fn(),
      onRequestPositionEdit: vi.fn(() => true),
      onSaveSpatialPosition,
    });
    fireEvent.click(await screen.findByRole("button", { name: "地理地点 marker" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑位置" }));

    const editableMarker = await screen.findByRole("button", {
      name: "地理地点 marker editable",
    });
    fireEvent.keyDown(editableMarker, { key: "ArrowRight" });
    expect(onSaveSpatialPosition).not.toHaveBeenCalled();
    expect(screen.getByText(/本地预览尚未写入/u)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));
    await waitFor(() =>
      expect(onSaveSpatialPosition).toHaveBeenCalledWith("loc_geo", {
        coordinate_system: "wgs84",
        latitude: 31.23,
        longitude: 121.4701,
      }),
    );
    expect(onPositionEditStateChange).toHaveBeenLastCalledWith(false, false);
  });

  it("cancels a position preview without writing", async () => {
    const onSaveSpatialPosition = vi.fn(async () => "saved" as const);
    renderMap({
      onReloadSpatialLocation: vi.fn(),
      onRequestPositionEdit: vi.fn(() => true),
      onSaveSpatialPosition,
    });
    fireEvent.click(await screen.findByRole("button", { name: "地理地点 marker" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑位置" }));
    fireEvent.keyDown(
      await screen.findByRole("button", { name: "地理地点 marker editable" }),
      { key: "ArrowRight" },
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(onSaveSpatialPosition).not.toHaveBeenCalled();
    expect(screen.getByText("31.23000, 121.47000")).toBeInTheDocument();
  });

  it("converts an inferred topology preview into schematic coordinates", async () => {
    const onSaveSpatialPosition = vi.fn(async () => "saved" as const);
    renderMap({
      map: topologyMapModel(),
      selectedObjectId: "loc_inferred",
      onReloadSpatialLocation: vi.fn(),
      onRequestPositionEdit: vi.fn(() => true),
      onSaveSpatialPosition,
    });

    const marker = await screen.findByRole("button", { name: "推算地点 marker" });
    fireEvent.click(marker);
    fireEvent.click(screen.getByRole("button", { name: "确认推算位置" }));
    fireEvent.keyDown(
      await screen.findByRole("button", { name: "推算地点 marker editable" }),
      { key: "ArrowRight" },
    );
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));

    await waitFor(() =>
      expect(onSaveSpatialPosition).toHaveBeenCalledWith("loc_inferred", {
        coordinate_system: "schematic",
        x: 55.5,
        y: 45,
      }),
    );
  });

  it("retains the preview across a revision conflict and reviews the latest position", async () => {
    const onSaveSpatialPosition = vi
      .fn()
      .mockResolvedValueOnce("conflict" as const)
      .mockResolvedValueOnce("saved" as const);
    const onReloadSpatialLocation = vi.fn(async () => ({
      found: true,
      position: {
        coordinate_system: "wgs84" as const,
        latitude: 31.23,
        longitude: 121.48,
      },
      revision: 8,
    }));
    renderMap({
      onReloadSpatialLocation,
      onRequestPositionEdit: vi.fn(() => true),
      onSaveSpatialPosition,
    });
    fireEvent.click(await screen.findByRole("button", { name: "地理地点 marker" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑位置" }));
    fireEvent.keyDown(
      await screen.findByRole("button", { name: "地理地点 marker editable" }),
      { key: "ArrowRight" },
    );
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));

    const reviewButton = await screen.findByRole("button", { name: "核对最新版" });
    expect(screen.getByText(/本地预览已保留/u)).toBeInTheDocument();
    fireEvent.click(reviewButton);
    expect(await screen.findByText(/最新版已修改此地点坐标/u)).toBeInTheDocument();
    expect(screen.getAllByText(/121.47010/u)).not.toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));
    await waitFor(() => expect(onSaveSpatialPosition).toHaveBeenCalledTimes(2));
  });

  it("retains a failed preview and keeps candidate or fixture maps read-only", async () => {
    const onSaveSpatialPosition = vi.fn(async () => "error" as const);
    const { rerender, props } = renderMap({
      onReloadSpatialLocation: vi.fn(),
      onRequestPositionEdit: vi.fn(() => true),
      onSaveSpatialPosition,
    });
    fireEvent.click(await screen.findByRole("button", { name: "地理地点 marker" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑位置" }));
    fireEvent.keyDown(
      await screen.findByRole("button", { name: "地理地点 marker editable" }),
      { key: "ArrowRight" },
    );
    fireEvent.click(screen.getByRole("button", { name: "保存位置" }));
    expect(await screen.findByText(/位置未保存/u)).toBeInTheDocument();
    expect(screen.getAllByText(/121.47010/u)).not.toHaveLength(0);

    rerender(
      <SpatialMapView
        {...props}
        onReloadSpatialLocation={undefined}
        onSaveSpatialPosition={undefined}
        readOnlyReason="候选预览只读；采用后才能编辑位置。"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByText(/候选预览只读/u)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑位置" })).toBeNull();
  });
});
