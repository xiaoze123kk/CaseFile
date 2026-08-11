import type { CaseFile } from "@casefile/contracts";
import { describe, expect, it } from "vitest";

import restartLoopFixture from "../../../fixtures/casefiles/restart_loop.casefile.json";
import {
  buildObjectDetailModel,
} from "@/features/analyst-workbench/workbench-object-detail-model";
import {
  classificationLabel,
  confidenceLabel,
  confirmationStatusLabel,
  formatCaseClock,
  formatCaseWallClock,
  objectSubtypeLabel,
  reliabilityLabel,
  serializeCaseWallClock,
} from "@/features/analyst-workbench/workbench-presenters";

const document = restartLoopFixture as unknown as CaseFile;

describe("workbench object detail model", () => {
  it("localizes known and unknown enum values", () => {
    expect(objectSubtypeLabel("person")).toBe("人物");
    expect(objectSubtypeLabel("not_a_contract_value")).toBe("其他");
    expect(reliabilityLabel("high")).toBe("高");
    expect(reliabilityLabel("not_a_contract_value")).toBe("未标注");
    expect(classificationLabel("key")).toBe("关键线索");
    expect(classificationLabel("not_a_contract_value")).toBe("其他信息");
    expect(confirmationStatusLabel("user_confirmed")).toBe("作者已确认");
    expect(confidenceLabel(0.925)).toBe("置信度 93%");
    expect(confidenceLabel(null)).toBe("置信度未标注");
  });

  it("formats and serializes case wall-clock time without timezone conversion", () => {
    const original = "2042-06-01T20:00:17.125+08:00";

    expect(formatCaseWallClock(original)).toBe("2042年6月1日 20:00");
    expect(formatCaseClock(original)).toBe("20:00");
    expect(serializeCaseWallClock("2042-06-02", "01:05", original)).toBe(
      "2042-06-02T01:05:17.125+08:00",
    );
    expect(formatCaseWallClock("unknown")).toBe("时间未定");
  });

  it("builds readable browse models for all five production object types", () => {
    const selections = [
      ["ent_researcher", "实体", "人物"],
      ["info_restart_log", "信息", "系统日志"],
      ["evt_restart_seven", "事件", "既定事实"],
      ["loc_lab", "地点", "示意位置"],
      ["hyp_automatic_restart", "假设", "已支持"],
    ] as const;

    for (const [id, kindLabel, subtypeLabel] of selections) {
      const model = buildObjectDetailModel(document, id);
      expect(model).not.toBeNull();
      expect(model).toMatchObject({ id, kindLabel, subtypeLabel });
      expect(model?.coreSections.length).toBeGreaterThan(0);
      expect(model?.technicalDetails.some((item) => item.label === "稳定编号")).toBe(true);
    }
  });

  it("resolves directory references, keeps non-directory references readable, and labels missing targets", () => {
    const withMissingReference: CaseFile = {
      ...document,
      events: document.events.map((event) =>
        event.id === "evt_restart_seven"
          ? {
              ...event,
              participant_refs: [
                ...event.participant_refs,
                { object_type: "entity", object_id: "ent_missing" },
              ],
            }
          : event,
      ),
    };
    const event = buildObjectDetailModel(withMissingReference, "evt_restart_seven");
    const information = buildObjectDetailModel(document, "info_restart_log");

    const participantField = event?.coreSections
      .flatMap((section) => section.fields)
      .find((field) => field.label === "参与者");
    expect(participantField).toMatchObject({ kind: "references" });
    if (participantField?.kind === "references") {
      expect(participantField.references).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ id: "ent_backup_system", selectable: true }),
          expect.objectContaining({ id: "ent_missing", label: "已缺失的实体", missing: true }),
        ]),
      );
    }
    expect(information?.references).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "claim_backup_trigger", label: "备用系统触发重启", selectable: false }),
      ]),
    );
    expect(information?.sourceReferences).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ label: "来源片段尚未载入", missing: true }),
      ]),
    );
  });

  it("surfaces structure-lock declarations with localized field paths", () => {
    const withEventLock: CaseFile = {
      ...document,
      structure_locks: [
        ...document.structure_locks,
        {
          ...document.structure_locks[0],
          id: "lock_event_time",
          title: "时间线锁定",
          object_ref: { object_type: "event", object_id: "evt_restart_seven" },
          field_paths: ["/time", "/participant_refs"],
          reason: "事件时间已由作者确认。",
        },
      ],
    };

    expect(buildObjectDetailModel(withEventLock, "evt_restart_seven")?.structureLocks).toEqual([
      {
        title: "时间线锁定",
        reason: "事件时间已由作者确认。",
        fields: ["卷宗时间", "参与者"],
      },
    ]);
  });
});
