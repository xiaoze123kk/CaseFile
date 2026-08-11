import type { CaseFile, CoreMetadata, ObjectRef } from "@casefile/contracts";
import { describe, expect, it } from "vitest";

import {
  buildWorkbenchSpatialModel,
  defaultSpatialLayerVisibility,
  filterWorkbenchSpatialView,
} from "@/features/analyst-workbench/workbench-spatial-model";
import type { WorkbenchTimelineEvent } from "@/features/analyst-workbench/workbench-real-data-types";

function ref(object_type: string, object_id: string): ObjectRef {
  return { object_type, object_id };
}

function metadata(): CoreMetadata {
  return {
    description: "",
    tags: [],
    source_refs: [],
    confidence: null,
    confirmation_status: "unresolved",
    created_by: { actor_type: "user", actor_id: "tester" },
    updated_at: "2026-08-11T08:00:00Z",
    revision: 1,
  };
}

function location(
  id: string,
  input: {
    position?: CaseFile["locations"][number]["spatial_position"];
    parentId?: string;
    adjacentIds?: string[];
    travelTimes?: Array<{ toId: string; minutes: number }>;
  } = {},
): CaseFile["locations"][number] {
  return {
    ...metadata(),
    id,
    name: id,
    parent_ref: input.parentId ? ref("location", input.parentId) : null,
    adjacency_refs: (input.adjacentIds ?? []).map((id) => ref("location", id)),
    access_rules: [],
    travel_times: (input.travelTimes ?? []).map((travelTime) => ({
      to_ref: ref("location", travelTime.toId),
      minutes: travelTime.minutes,
    })),
    visibility_rules: [],
    ...(input.position ? { spatial_position: input.position } : {}),
  };
}

function caseFile(locations: CaseFile["locations"]): CaseFile {
  return {
    schema_version: "2.0",
    casefile_id: "case_spatial_test",
    title: "空间测试卷宗",
    status: "draft",
    version: {
      version_id: "version_spatial_test",
      version_no: 1,
      parent_version_id: null,
    },
    brief_ref: { brief_id: "brief_spatial_test", version: 1 },
    resolution_specs: [],
    entities: [],
    relationships: [],
    locations,
    events: [],
    information_units: [],
    claims: [],
    hypotheses: [],
    reasoning_paths: [],
    constraints: [],
    structure_locks: [],
    content_notices: [],
    extensions: {},
  };
}

function timelineEvent(
  id: string,
  locationId: string,
  relatedObjectIds: string[] = [],
): WorkbenchTimelineEvent {
  return {
    id,
    time: "第 2 日 09:30",
    label: id,
    location: locationId,
    summary: "",
    relatedObjectIds,
    issueIds: [],
    start: "第 2 日 09:30",
    end: null,
    precision: "minute",
    truthStatus: "reported",
    sortKey: null,
    refs: {
      participantIds: [],
      locationId,
      causeIds: [],
      effectIds: [],
      observerIds: [],
      sourceIds: [],
    },
    source: null,
  };
}

describe("workbench spatial model", () => {
  it("keeps WGS84 as latitude and longitude without percentage coordinates", () => {
    const model = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_geo", {
          position: {
            coordinate_system: "wgs84",
            latitude: 31.2304,
            longitude: 121.4737,
          },
        }),
      ]),
      [],
    );

    expect(model.availableModes).toEqual(["geographic"]);
    expect(model.defaultMode).toBe("geographic");
    expect(model.views.geographic.locations[0].position).toEqual({
      kind: "wgs84",
      latitude: 31.2304,
      longitude: 121.4737,
    });
    expect(model.views.geographic.locations[0].position).not.toHaveProperty("x");
  });

  it("separates all three modes and uses explicit scene coordinates as topology anchors", () => {
    const document = caseFile([
      location("loc_geo", {
        position: {
          coordinate_system: "wgs84",
          latitude: 31,
          longitude: 121,
        },
      }),
      location("loc_anchor", {
        adjacentIds: ["loc_inferred"],
        position: { coordinate_system: "schematic", x: 18, y: 26 },
      }),
      location("loc_inferred", { parentId: "loc_anchor" }),
    ]);
    const model = buildWorkbenchSpatialModel(document, []);

    expect(model.availableModes).toEqual([
      "geographic",
      "scene",
      "topology",
    ]);
    expect(model.views.geographic.locations.map((item) => item.locationId)).toEqual([
      "loc_geo",
    ]);
    expect(model.views.scene.locations.map((item) => item.locationId)).toEqual([
      "loc_anchor",
    ]);
    expect(model.views.topology.locations).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ locationId: "loc_anchor", source: "schematic" }),
        expect.objectContaining({ locationId: "loc_inferred", source: "inferred" }),
      ]),
    );
    expect(
      model.views.topology.locations.find((item) => item.locationId === "loc_anchor")
        ?.position,
    ).toEqual({ kind: "planar", x: 18, y: 26 });
  });

  it("produces the same topology for the same document", () => {
    const document = caseFile([
      location("loc_anchor", {
        adjacentIds: ["loc_beta", "loc_alpha"],
        position: { coordinate_system: "schematic", x: 50, y: 50 },
      }),
      location("loc_beta", { parentId: "loc_anchor" }),
      location("loc_alpha", { parentId: "loc_anchor" }),
    ]);

    expect(buildWorkbenchSpatialModel(document, []).views.topology).toEqual(
      buildWorkbenchSpatialModel(document, []).views.topology,
    );
  });

  it("does not assign a random point to a disconnected location", () => {
    const model = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_anchor", {
          position: { coordinate_system: "schematic", x: 40, y: 40 },
        }),
        location("loc_disconnected"),
      ]),
      [],
    );

    expect(model.unlocatedLocationIds).toEqual(["loc_disconnected"]);
    expect(
      Object.values(model.views).flatMap((view) =>
        view.locations.filter((item) => item.locationId === "loc_disconnected"),
      ),
    ).toEqual([]);
    expect(model.availableModes).toEqual(["scene"]);
  });

  it("aggregates events and related objects under one primary location marker", () => {
    const document = caseFile([
      location("loc_room", {
        position: { coordinate_system: "schematic", x: 30, y: 60 },
      }),
    ]);
    const model = buildWorkbenchSpatialModel(document, [
      timelineEvent("evt_a", "loc_room", ["loc_room", "ent_a"]),
      timelineEvent("evt_b", "loc_room", ["loc_room", "ent_b", "ent_a"]),
    ]);
    const marker = model.views.scene.locations[0];

    expect(model.views.scene.locations).toHaveLength(1);
    expect(marker.events.map((event) => event.eventId)).toEqual(["evt_a", "evt_b"]);
    expect(marker.relatedObjectIds).toEqual(["ent_a", "ent_b"]);
    expect(model.counts.events).toBe(2);
  });

  it("uses geographic, scene, then topology as the fixed default priority", () => {
    const geographic = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_geo", {
          position: {
            coordinate_system: "wgs84",
            latitude: 31,
            longitude: 121,
          },
        }),
        location("loc_scene", {
          position: { coordinate_system: "schematic", x: 10, y: 10 },
        }),
      ]),
      [],
    );
    const scene = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_scene", {
          position: { coordinate_system: "schematic", x: 10, y: 10 },
        }),
      ]),
      [],
    );
    const topology = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_a", { adjacentIds: ["loc_b"] }),
        location("loc_b", { adjacentIds: ["loc_a"] }),
      ]),
      [],
    );

    expect(geographic.defaultMode).toBe("geographic");
    expect(scene.defaultMode).toBe("scene");
    expect(topology.availableModes).toEqual(["topology"]);
    expect(topology.defaultMode).toBe("topology");
  });

  it("deduplicates undirected adjacency, preserves directed travel, and separates relations by mode", () => {
    const model = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_geo_a", {
          adjacentIds: ["loc_geo_b"],
          travelTimes: [
            { toId: "loc_geo_b", minutes: 6 },
            { toId: "loc_geo_b", minutes: 6 },
          ],
          position: {
            coordinate_system: "wgs84",
            latitude: 31,
            longitude: 121,
          },
        }),
        location("loc_geo_b", {
          adjacentIds: ["loc_geo_a"],
          travelTimes: [{ toId: "loc_geo_a", minutes: 8 }],
          position: {
            coordinate_system: "wgs84",
            latitude: 31.1,
            longitude: 121.1,
          },
        }),
        location("loc_scene_a", {
          adjacentIds: ["loc_scene_b"],
          position: { coordinate_system: "schematic", x: 20, y: 20 },
        }),
        location("loc_scene_b", {
          adjacentIds: ["loc_scene_a"],
          position: { coordinate_system: "schematic", x: 70, y: 70 },
        }),
      ]),
      [],
    );

    expect(model.views.geographic.relations).toEqual([
      expect.objectContaining({
        relationId: "adjacency:loc_geo_a:loc_geo_b",
        direction: "undirected",
      }),
      expect.objectContaining({
        relationId: "travel:loc_geo_a:loc_geo_b:6",
        direction: "directed",
        label: "6 分钟",
      }),
      expect.objectContaining({
        relationId: "travel:loc_geo_b:loc_geo_a:8",
        direction: "directed",
        label: "8 分钟",
      }),
    ]);
    expect(model.views.scene.relations).toEqual([
      expect.objectContaining({ relationId: "adjacency:loc_scene_a:loc_scene_b" }),
    ]);
    expect(model.views.geographic.relations).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({ toLocationId: "loc_scene_b" }),
      ]),
    );
  });

  it("filters explicit, inferred, and relationship layers without changing model facts", () => {
    const model = buildWorkbenchSpatialModel(
      caseFile([
        location("loc_anchor", {
          adjacentIds: ["loc_inferred"],
          position: { coordinate_system: "schematic", x: 35, y: 40 },
        }),
        location("loc_inferred", { adjacentIds: ["loc_anchor"] }),
      ]),
      [timelineEvent("evt_anchor", "loc_anchor")],
    );
    const relationsOn = {
      ...defaultSpatialLayerVisibility,
      relations: true,
    };
    const explicitOnly = filterWorkbenchSpatialView(
      model.views.topology,
      relationsOn,
    );
    const withInferred = filterWorkbenchSpatialView(model.views.topology, {
      ...relationsOn,
      unconfirmed: true,
    });
    const inferredOnly = filterWorkbenchSpatialView(model.views.topology, {
      ...relationsOn,
      locations: false,
      unconfirmed: true,
    });

    expect(explicitOnly.locations.map((location) => location.locationId)).toEqual([
      "loc_anchor",
    ]);
    expect(explicitOnly.relations).toEqual([]);
    expect(withInferred.locations).toHaveLength(2);
    expect(withInferred.relations).toHaveLength(1);
    expect(inferredOnly.locations.map((location) => location.locationId)).toEqual([
      "loc_inferred",
    ]);
    expect(inferredOnly.relations).toEqual([]);
    expect(model.views.topology.locations).toHaveLength(2);
    expect(model.counts.events).toBe(1);
  });
});
