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

function mapModel(): WorkbenchMapModel {
  return {
    availableModes: ["geographic", "scene"],
    defaultMode: "geographic",
    views: {
      geographic: { mode: "geographic", locations: [geographicLocation] },
      scene: { mode: "scene", locations: [sceneLocation] },
      topology: { mode: "topology", locations: [] },
    },
    unlocatedLocationIds: ["loc_missing"],
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
      render(view, selection, callbacks) {
        container.replaceChildren();
        for (const location of view.locations) {
          const marker = document.createElement("button");
          marker.type = "button";
          marker.textContent = location.label;
          marker.setAttribute("aria-label", `${location.label} marker`);
          marker.setAttribute(
            "aria-pressed",
            String(
              selection.activeSpatialId === location.spatialId ||
                selection.selectedLocationId === location.locationId,
            ),
          );
          marker.addEventListener("click", () => callbacks.onActivateLocation(location));
          marker.addEventListener("keydown", (event) => {
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
});
