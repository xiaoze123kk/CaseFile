import type { CaseFile, CoreMetadata, ObjectRef } from "@casefile/contracts";
import { describe, expect, it } from "vitest";

import { defaultWorkbenchSeed } from "@/features/analyst-workbench/analyst-fixture";
import type { WorkbenchValidationView } from "@/lib/api-client";
import {
  mapCaseFileToWorkbenchModel,
  mapFixtureToWorkbenchModel,
  type WorkbenchModel,
} from "@/features/analyst-workbench/workbench-real-data";

function ref(object_type: string, object_id: string): ObjectRef {
  return { object_type, object_id };
}

function metadata(
  overrides: Partial<CoreMetadata> = {},
): CoreMetadata {
  return {
    description: "真实对象说明",
    tags: [],
    source_refs: [],
    confidence: 0.8,
    confirmation_status: "ai_inferred",
    created_by: { actor_type: "agent", actor_id: "agent_brief_to_draft" },
    updated_at: "2026-08-07T08:00:00Z",
    revision: 1,
    ...overrides,
  };
}

function makeCaseFile(): CaseFile {
  return {
    schema_version: "2.0",
    casefile_id: "case_real_workbench",
    title: "真实卷宗",
    status: "draft",
    version: {
      version_id: "version_real_workbench",
      version_no: 1,
      parent_version_id: null,
    },
    brief_ref: { brief_id: "brief_real_workbench", version: 3 },
    resolution_specs: [
      {
        ...metadata(),
        id: "res_core_question",
        title: "核心问题",
        question_type: "causal_explanation",
        reasoning_question: "是谁改写了记录？",
        conclusion_mode: "unique",
        required_slots: [],
        accepted_answers: [],
        required_claim_refs: [ref("claim", "claim_record_changed")],
      },
    ],
    entities: [
      {
        ...metadata({ confirmation_status: "user_confirmed", confidence: 1 }),
        id: "ent_analyst",
        entity_type: "person",
        name: "调查员",
        aliases: [],
        traits: ["谨慎"],
        goals: ["复原记录"],
        secrets: [],
        capabilities: [],
        knowledge_states: [],
      },
      {
        ...metadata({ confidence: 0.55 }),
        id: "ent_operator",
        entity_type: "person",
        name: "值班员",
        aliases: [],
        traits: [],
        goals: [],
        secrets: [],
        capabilities: [],
        knowledge_states: [],
      },
    ],
    relationships: [
      {
        ...metadata(),
        id: "rel_analyst_operator",
        title: "询问",
        from_ref: ref("entity", "ent_analyst"),
        to_ref: ref("entity", "ent_operator"),
        relationship_type: "investigates",
        direction: "directed",
        truth_status: "canon_true",
        visibility: "public",
      },
    ],
    locations: [
      {
        ...metadata(),
        id: "loc_schematic_gate",
        name: "示意入口",
        parent_ref: null,
        adjacency_refs: [ref("location", "loc_schematic_room")],
        access_rules: [],
        travel_times: [
          { to_ref: ref("location", "loc_schematic_room"), minutes: 5 },
        ],
        visibility_rules: [],
        spatial_position: {
          coordinate_system: "schematic",
          x: 10,
          y: 20,
        },
      },
      {
        ...metadata(),
        id: "loc_schematic_room",
        name: "无坐标机房",
        parent_ref: ref("location", "loc_schematic_gate"),
        adjacency_refs: [ref("location", "loc_schematic_gate")],
        access_rules: [],
        travel_times: [],
        visibility_rules: [],
      },
      {
        ...metadata(),
        id: "loc_geo_north",
        name: "北侧地理点",
        parent_ref: null,
        adjacency_refs: [],
        access_rules: [],
        travel_times: [],
        visibility_rules: [],
        spatial_position: {
          coordinate_system: "wgs84",
          latitude: 31,
          longitude: 121,
        },
      },
      {
        ...metadata(),
        id: "loc_geo_south",
        name: "南侧地理点",
        parent_ref: null,
        adjacency_refs: [],
        access_rules: [],
        travel_times: [],
        visibility_rules: [],
        spatial_position: {
          coordinate_system: "wgs84",
          latitude: 30,
          longitude: 120,
        },
      },
    ],
    events: [
      {
        ...metadata(),
        id: "evt_late",
        title: "后发生事件",
        truth_status: "canon_true",
        time: {
          kind: "exact",
          value: "2026-08-07T10:00",
          precision: "minute",
        },
        participant_refs: [ref("entity", "ent_operator")],
        location_ref: ref("location", "loc_geo_north"),
        cause_refs: [ref("event", "evt_early")],
        effect_refs: [],
        observed_by_refs: [],
      },
      {
        ...metadata(),
        id: "evt_early",
        title: "先发生事件",
        truth_status: "reported",
        time: {
          kind: "range",
          start: "2026-08-07T09:00",
          end: "2026-08-07T09:05",
          precision: "minute",
        },
        participant_refs: [ref("entity", "ent_analyst")],
        location_ref: ref("location", "loc_schematic_gate"),
        cause_refs: [],
        effect_refs: [ref("event", "evt_late")],
        observed_by_refs: [ref("entity", "ent_operator")],
      },
      {
        ...metadata(),
        id: "evt_unknown_time",
        title: "时间未定事件",
        truth_status: "unknown",
        time: { kind: "unknown" },
        participant_refs: [],
        location_ref: ref("location", "loc_schematic_room"),
        cause_refs: [],
        effect_refs: [],
        observed_by_refs: [],
      },
    ],
    information_units: [
      {
        ...metadata({
          source_refs: [ref("source_fragment", "src_gate_log")],
          confidence: 0.92,
        }),
        id: "info_gate_log",
        information_type: "system_log",
        title: "门禁记录",
        content: "记录在九点被改写。",
        source_event_ref: ref("event", "evt_early"),
        reliability: "high",
        truth_status: "canon_true",
        supports_claim_refs: [ref("claim", "claim_record_changed")],
        refutes_claim_refs: [],
        availability: {
          perspective_refs: [ref("entity", "ent_analyst")],
          acquisition_conditions: [],
          alternative_path_refs: [],
        },
        classification: "key",
      },
    ],
    claims: [
      {
        ...metadata(),
        id: "claim_record_changed",
        title: "记录被改写",
        statement: "门禁记录在事件后被改写。",
        claim_type: "fact",
        support_refs: [ref("information_unit", "info_gate_log")],
        refute_refs: [],
        dependency_claim_refs: [],
        status: "supported",
        materiality: "critical",
      },
    ],
    hypotheses: [
      {
        ...metadata(),
        id: "hyp_operator_changed_record",
        title: "值班员改写记录",
        proposition: "值班员在事件后修改了门禁记录。",
        target_resolution_ref: ref("resolution_spec", "res_core_question"),
        required_claim_refs: [ref("claim", "claim_record_changed")],
        falsifier_refs: [],
        competing_hypothesis_refs: [],
        status: "accepted",
        score: 0.9,
      },
    ],
    reasoning_paths: [
      {
        ...metadata(),
        id: "path_record_change",
        title: "记录改写路径",
        path_type: "causal",
        target_ref: ref("hypothesis", "hyp_operator_changed_record"),
        steps: [
          {
            step_id: "step_log_to_claim",
            input_refs: [ref("information_unit", "info_gate_log")],
            operation: "infer",
            output_ref: ref("claim", "claim_record_changed"),
          },
        ],
        required_for_resolution: true,
        alternative_path_refs: [],
      },
    ],
    constraints: [],
    structure_locks: [],
    content_notices: [],
    extensions: {},
  };
}

function expectWorkbenchSeedCompatibility(model: WorkbenchModel) {
  const compatible = model satisfies import("@/features/analyst-workbench/analyst-fixture").WorkbenchSeed;
  return compatible;
}

describe("real workbench data mapper", () => {
  it("maps six real object collections, stable timeline refs, and deterministic graph data", () => {
    const caseFile = makeCaseFile();
    const model = expectWorkbenchSeedCompatibility(
      mapCaseFileToWorkbenchModel(caseFile, 7),
    );

    expect(model.origin).toBe("contract");
    expect(model.draftRevision).toBe(7);
    expect(model.caseMeta.revision).toBe("R7");
    expect(model.objectCounts).toEqual({
      resolution_spec: 1,
      entity: 2,
      information: 1,
      event: 3,
      location: 4,
      hypothesis: 1,
    });
    expect(new Set(model.caseObjects.map((object) => object.kind))).toEqual(
      new Set(["resolution_spec", "entity", "information", "event", "location", "hypothesis"]),
    );
    expect(model.timelineEvents.map((event) => event.id)).toEqual([
      "evt_early",
      "evt_late",
      "evt_unknown_time",
    ]);
    expect(model.timelineEvents[0].refs).toMatchObject({
      participantIds: ["ent_analyst"],
      locationId: "loc_schematic_gate",
      effectIds: ["evt_late"],
      observerIds: ["ent_operator"],
    });
    expect(
      model.caseObjects.find((object) => object.id === "info_gate_log")
        ?.relatedEventIds,
    ).toEqual(["evt_early"]);
    expect(
      model.caseObjects.find((object) => object.id === "ent_analyst")
        ?.relatedEventIds,
    ).toEqual(["evt_early"]);
    expect(
      model.caseObjects.find((object) => object.id === "ent_operator")
        ?.relatedEventIds,
    ).toEqual(["evt_early", "evt_late"]);
    expect(
      model.caseObjects.find((object) => object.id === "loc_geo_north")
        ?.relatedEventIds,
    ).toEqual(["evt_late"]);
    expect(
      model.caseObjects.find(
        (object) => object.id === "hyp_operator_changed_record",
      )?.relatedEventIds,
    ).toEqual([]);

    expect(model.graphEdges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          kind: "relationship",
          from: "ent_analyst",
          to: "ent_operator",
        }),
        expect.objectContaining({
          kind: "event_location",
          from: "evt_early",
          to: "loc_schematic_gate",
        }),
        expect.objectContaining({
          kind: "information_support",
          from: "info_gate_log",
          to: "claim_record_changed",
        }),
      ]),
    );
    const nodeIds = new Set(model.graphNodes.map((node) => node.id));
    for (const edge of model.graphEdges) {
      expect(nodeIds.has(edge.from)).toBe(true);
      expect(nodeIds.has(edge.to)).toBe(true);
    }
    expect(model.graphNodes).toEqual(
      mapCaseFileToWorkbenchModel(caseFile, 7).graphNodes,
    );
  });

  it("maps reasoning steps to the target hypothesis and resolution question", () => {
    const path = mapCaseFileToWorkbenchModel(makeCaseFile(), 7).reasoningPaths[0];

    expect(path).toMatchObject({
      id: "path_record_change",
      question: "是谁改写了记录？",
      hypothesisId: "hyp_operator_changed_record",
      targetHypothesisId: "hyp_operator_changed_record",
      resolutionSpecId: "res_core_question",
      conclusion: "该核心问题尚未形成结论",
      outcome: "supported",
      evidenceIds: ["info_gate_log"],
    });
    expect(path.steps[0]).toMatchObject({
      id: "step_log_to_claim",
      verb: "推断",
      operation: "infer",
      inputIds: ["info_gate_log"],
      outputId: "claim_record_changed",
    });
  });

  it("groups competing hypotheses in contract order and reads only explicit evidence assessments", () => {
    const caseFile = makeCaseFile();
    caseFile.hypotheses = [
      {
        ...caseFile.hypotheses[0],
        evidence_assessments: [
          {
            information_ref: ref("information_unit", "info_gate_log"),
            effect: "supports",
            strength: "strong",
            rationale: "改写时间与值班记录一致。",
          },
        ],
      },
      {
        ...caseFile.hypotheses[0],
        id: "hyp_external_changed_record",
        title: "外部人员改写记录",
        proposition: "外部人员在事件后修改了门禁记录。",
        evidence_assessments: [],
      },
    ];

    const model = mapCaseFileToWorkbenchModel(caseFile, 7);

    expect(model.reasoningGroups).toEqual([
      {
        resolutionSpecId: "res_core_question",
        question: "是谁改写了记录？",
        hypotheses: [
          expect.objectContaining({ id: "hyp_operator_changed_record" }),
          expect.objectContaining({ id: "hyp_external_changed_record" }),
        ],
        information: [
          { id: "info_gate_log", title: "门禁记录", reliability: "high" },
        ],
        assessments: [
          {
            hypothesisId: "hyp_operator_changed_record",
            informationId: "info_gate_log",
            effect: "supports",
            strength: "strong",
            rationale: "改写时间与值班记录一致。",
          },
        ],
      },
    ]);
  });

  it("falls back to a stable question title when the resolution has no title", () => {
    const caseFile = makeCaseFile();
    caseFile.resolution_specs[0] = {
      ...caseFile.resolution_specs[0],
      title: "",
      reasoning_question: "",
    };

    expect(mapCaseFileToWorkbenchModel(caseFile, 7).reasoningGroups[0]?.question).toBe(
      "未命名待解问题",
    );
  });

  it("separates geographic, scene, and deterministic topology data", () => {
    const caseFile = makeCaseFile();
    const model = mapCaseFileToWorkbenchModel(caseFile, 7);

    expect(model.map.availableModes).toEqual([
      "geographic",
      "scene",
      "topology",
    ]);
    expect(model.map.defaultMode).toBe("geographic");
    const north = model.map.views.geographic.locations.find(
      (location) => location.locationId === "loc_geo_north",
    );
    const south = model.map.views.geographic.locations.find(
      (location) => location.locationId === "loc_geo_south",
    );
    expect(north).toMatchObject({
      source: "wgs84",
      position: { kind: "wgs84", latitude: 31, longitude: 121 },
    });
    expect(north?.position).not.toHaveProperty("x");
    expect(south).toMatchObject({
      position: { kind: "wgs84", latitude: 30, longitude: 120 },
    });
    expect(north?.events).toEqual(
      expect.arrayContaining([expect.objectContaining({ eventId: "evt_late" })]),
    );

    const explicit = model.map.views.scene.locations.find(
      (location) => location.locationId === "loc_schematic_gate",
    );
    const inferred = model.map.views.topology.locations.find(
      (location) => location.locationId === "loc_schematic_room",
    );
    expect(explicit).toMatchObject({
      source: "schematic",
      position: { kind: "planar", x: 10, y: 20 },
    });
    expect(model.map.views.scene.locations).toHaveLength(1);
    expect(inferred).toMatchObject({ source: "inferred" });
    expect(model.map.unlocatedLocationIds).toEqual([]);
    expect(
      model.map.views.topology.locations.flatMap((location) => location.events),
    ).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ eventId: "evt_early" }),
        expect.objectContaining({ eventId: "evt_unknown_time" }),
      ]),
    );
    expect(model.map).toEqual(mapCaseFileToWorkbenchModel(caseFile, 7).map);
  });

  it("returns real empty states without manufacturing issues, sources, audit entries, or selections", () => {
    const populated = makeCaseFile();
    const empty: CaseFile = {
      ...populated,
      resolution_specs: [],
      entities: [],
      relationships: [],
      locations: [],
      events: [],
      information_units: [],
      claims: [],
      hypotheses: [],
      reasoning_paths: [],
    };
    const model = mapCaseFileToWorkbenchModel(empty, 1);

    expect(model.caseObjects).toEqual([]);
    expect(model.timelineEvents).toEqual([]);
    expect(model.graphNodes).toEqual([]);
    expect(model.graphEdges).toEqual([]);
    expect(model.reasoningPaths).toEqual([]);
    expect(model.map.availableModes).toEqual([]);
    expect(model.mapMarkers).toEqual([]);
    expect(model.mapLabels).toEqual([]);
    expect(model.validationIssues).toEqual([]);
    expect(model.initialAuditEntries).toEqual([]);
    expect(model.defaultEventId).toBeNull();
    expect(model.defaultObjectId).toBeNull();
    expect(model.defaultIssueId).toBeNull();
  });

  it("maps validator targets to stable event ids before timeline time sorting", () => {
    const validation: WorkbenchValidationView = {
      status: "failed",
      validator: "casefile.contracts.validate_casefile",
      schema_version: "1.0",
      issue_count: 1,
      issues: [
        {
          issue_id: "validator:stable-event",
          code: "missing_reference",
          path: "/events/0/location_ref",
          message: "引用的对象不存在",
          severity: "error",
          target: {
            object_ref: { object_type: "event", object_id: "evt_late" },
            field_path: "/location_ref",
          },
        },
      ],
      reason: null,
    };

    const model = mapCaseFileToWorkbenchModel(makeCaseFile(), 7, validation);

    expect(model.timelineEvents.map((event) => event.id)).toEqual([
      "evt_early",
      "evt_late",
      "evt_unknown_time",
    ]);
    expect(
      model.timelineEvents.find((event) => event.id === "evt_late")?.issueIds,
    ).toEqual(["validator:stable-event"]);
    expect(
      model.timelineEvents.find((event) => event.id === "evt_early")?.issueIds,
    ).toEqual([]);
    expect(model.validationIssues[0]).toMatchObject({
      id: "validator:stable-event",
      eventId: "evt_late",
      source: "validator",
      evidenceIds: [],
      patchBefore: "",
      patchAfter: "",
    });
  });

  it("maps one resolution conclusion across relationship, reasoning, and timeline selection data", () => {
    const caseFile = makeCaseFile();
    caseFile.resolution_specs[0] = {
      ...caseFile.resolution_specs[0],
      required_slots: [
        {
          slot_id: "slot_perpetrator",
          value_type: "entity_or_claim_ref",
          required: true,
        },
      ],
      conclusion: {
        outcome: "answer",
        review_status: "confirmed",
        summary: "值班员改写了记录。",
        values: [
          {
            slot_id: "slot_perpetrator",
            value: ref("entity", "ent_operator"),
          },
          {
            slot_id: "slot_unknown_detail",
            value: ref("entity", "ent_missing"),
          },
        ],
        selected_hypothesis_refs: [
          ref("hypothesis", "hyp_operator_changed_record"),
        ],
        supporting_reasoning_path_refs: [
          ref("reasoning_path", "path_record_change"),
        ],
        rationale: "门禁记录与值班时段相互印证。",
        unresolved_gaps: [],
      },
    };

    const model = mapCaseFileToWorkbenchModel(caseFile, 7);

    expect(model.conclusions).toEqual([
      expect.objectContaining({
        resolutionSpecId: "res_core_question",
        reviewStatus: "confirmed",
        summary: "值班员改写了记录。",
        values: [
          { label: "嫌疑人", value: "值班员" },
          { label: "解答信息", value: "关联对象" },
        ],
        selectedHypothesisIds: ["hyp_operator_changed_record"],
        supportingReasoningPathIds: ["path_record_change"],
        relatedEventIds: ["evt_early"],
      }),
    ]);
    expect(model.reasoningPaths[0]?.conclusion).toBe("值班员改写了记录。");
    expect(model.graphNodes).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "res_core_question" }),
      expect.objectContaining({
        id: "resolution-conclusion:res_core_question",
        directoryObjectId: "res_core_question",
        label: "值班员改写了记录。",
      }),
    ]));
    expect(model.graphEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({
        from: "res_core_question",
        to: "resolution-conclusion:res_core_question",
        kind: "resolution_conclusion",
      }),
      expect.objectContaining({
        from: "hyp_operator_changed_record",
        to: "resolution-conclusion:res_core_question",
        label: "进入当前结论",
      }),
      expect.objectContaining({
        from: "path_record_change",
        to: "resolution-conclusion:res_core_question",
        label: "收束依据",
      }),
    ]));
    expect(model.timelineEvents.map((event) => event.id)).toEqual([
      "evt_early",
      "evt_late",
      "evt_unknown_time",
    ]);
  });

  it("does not expose English-only generated text on Chinese workbench surfaces", () => {
    const caseFile = makeCaseFile();
    caseFile.information_units[0] = {
      ...caseFile.information_units[0],
      title: "Archive Access Logs",
      description: "Access logs showing who opened the archive.",
      content: "The archive was opened from an external address.",
    };
    caseFile.claims[0] = {
      ...caseFile.claims[0],
      title: "The Records Were Modified",
      description: "The files contain signs of manipulation.",
      statement: "The records were changed after creation.",
    };
    caseFile.hypotheses[0] = {
      ...caseFile.hypotheses[0],
      title: "The Manipulator Is an Insider",
      description: "An insider changed the records.",
      proposition: "An insider is responsible.",
      evidence_assessments: [
        {
          information_ref: ref("information_unit", "info_gate_log"),
          effect: "supports",
          strength: "strong",
          rationale: "The access log points to an insider.",
        },
      ],
    };
    caseFile.reasoning_paths[0] = {
      ...caseFile.reasoning_paths[0],
      title: "Internal Manipulator Reasoning Path",
      description: "A reasoning chain for the insider hypothesis.",
    };

    const model = mapCaseFileToWorkbenchModel(caseFile, 7);
    const information = model.caseObjects.find((item) => item.id === "info_gate_log");

    expect(information?.label).toBe("信息 1（标题待补充）");
    expect(information?.description).toBe("该信息的创作说明待补充。");
    expect(model.graphNodes.find((item) => item.id === "claim_record_changed")?.label)
      .toBe("论断 1（标题待补充）");
    expect(model.reasoningPaths[0]).toMatchObject({
      conclusion: "该核心问题尚未形成结论",
      title: "推理路径 1（标题待补充）",
    });
    expect(model.reasoningPaths[0].steps[0].claim).not.toMatch(/[A-Za-z]{2,}/);
    expect(model.reasoningGroups[0].hypotheses[0].title)
      .toBe("假设 1（标题待补充）");
    expect(model.reasoningGroups[0].information[0].title)
      .toBe("信息 1（标题待补充）");
    expect(model.reasoningGroups[0].assessments[0].rationale)
      .toBe("该判定依据待补充。");
  });

  it("projects a relative event onto an existing wall-clock anchor without changing its source semantics", () => {
    const caseFile = makeCaseFile();
    caseFile.events = [
      {
        ...caseFile.events[0],
        id: "evt_anchor",
        title: "锚点事件",
        time: {
          kind: "exact",
          value: "2042-06-01T20:00",
          precision: "minute",
        },
      },
      {
        ...caseFile.events[1],
        id: "evt_follow_up",
        title: "后续事件",
        time: {
          kind: "relative",
          anchor_event_ref: ref("event", "evt_anchor"),
          relation: "after",
          offset_minutes: 15,
        },
      },
    ];

    const timeline = mapCaseFileToWorkbenchModel(caseFile, 7).timelineEvents;
    const followUp = timeline.find((event) => event.id === "evt_follow_up");

    expect(timeline.map((event) => event.id)).toEqual(["evt_anchor", "evt_follow_up"]);
    expect(followUp).toMatchObject({
      timeProjection: "relative-resolved",
      time: "相对 15 分钟之后",
      start: "2042-06-01T20:15:00",
      sortKey: "2042-06-01T20:15:00",
    });
    expect(followUp?.time).not.toContain("evt_anchor");
    expect(followUp?.source?.time).toEqual({
      kind: "relative",
      anchor_event_ref: ref("event", "evt_anchor"),
      relation: "after",
      offset_minutes: 15,
    });
  });

  it("propagates an uncertain anchor range into a projected relative time range", () => {
    const caseFile = makeCaseFile();
    caseFile.events = [
      {
        ...caseFile.events[0],
        id: "evt_anchor",
        title: "区间锚点事件",
        time: {
          kind: "range",
          start: "2042-06-01T20:00",
          end: "2042-06-01T20:10",
          precision: "minute",
        },
      },
      {
        ...caseFile.events[1],
        id: "evt_after",
        title: "锚点之后事件",
        time: {
          kind: "relative",
          anchor_event_ref: ref("event", "evt_anchor"),
          relation: "after",
          offset_minutes: 15,
        },
      },
      {
        ...caseFile.events[2],
        id: "evt_before",
        title: "锚点之前事件",
        time: {
          kind: "relative",
          anchor_event_ref: ref("event", "evt_anchor"),
          relation: "before",
          offset_minutes: 10,
        },
      },
    ];

    const timeline = mapCaseFileToWorkbenchModel(caseFile, 7).timelineEvents;

    expect(timeline.find((event) => event.id === "evt_after")).toMatchObject({
      timeProjection: "relative-resolved",
      start: "2042-06-01T20:15:00",
      end: "2042-06-01T20:25:00",
      sortKey: "2042-06-01T20:15:00",
    });
    expect(timeline.find((event) => event.id === "evt_before")).toMatchObject({
      timeProjection: "relative-resolved",
      start: "2042-06-01T19:50:00",
      end: "2042-06-01T20:00:00",
      sortKey: "2042-06-01T19:50:00",
    });
  });

  it("projects a same_time relative event with a null offset onto the anchor bounds", () => {
    const caseFile = makeCaseFile();
    caseFile.events = [
      {
        ...caseFile.events[0],
        id: "evt_anchor",
        title: "区间锚点事件",
        time: {
          kind: "range",
          start: "2042-06-01T20:00",
          end: "2042-06-01T20:10",
          precision: "minute",
        },
      },
      {
        ...caseFile.events[1],
        id: "evt_same_time",
        title: "同时发生事件",
        time: {
          kind: "relative",
          anchor_event_ref: ref("event", "evt_anchor"),
          relation: "same_time",
          offset_minutes: null,
        },
      },
    ];

    const timeline = mapCaseFileToWorkbenchModel(caseFile, 7).timelineEvents;

    expect(timeline.find((event) => event.id === "evt_same_time")).toMatchObject({
      timeProjection: "relative-resolved",
      start: "2042-06-01T20:00:00",
      end: "2042-06-01T20:10:00",
      sortKey: "2042-06-01T20:00:00",
    });
  });

  it("keeps the existing fixture model available through an explicit adapter", () => {
    const model = expectWorkbenchSeedCompatibility(
      mapFixtureToWorkbenchModel(defaultWorkbenchSeed),
    );

    expect(model.origin).toBe("fixture");
    expect(model.caseFile).toBeNull();
    expect(model.caseObjects.some((object) => object.kind === "entity")).toBe(true);
    expect(
      model.caseObjects.some((object) => object.kind === "information"),
    ).toBe(true);
    expect(model.graphNodes.map(({ x, y }) => ({ x, y }))).toEqual(
      defaultWorkbenchSeed.graphNodes.map(({ x, y }) => ({ x, y })),
    );
    expect(model.validationIssues).toEqual(defaultWorkbenchSeed.validationIssues);
    expect(model.defaultEventId).toBe(defaultWorkbenchSeed.defaultEventId);
  });
});
