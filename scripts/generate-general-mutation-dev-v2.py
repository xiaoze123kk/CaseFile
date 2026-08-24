"""Generate the frozen public 40-task General Mutation Dev v2 bank."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures/general_mutation_benchmark/capability/v2"
FIXTURE = ROOT / "fixtures/casefiles/general_mutation_dev_v2.casefile.json"
BASE_FIXTURE = ROOT / "fixtures/casefiles/restart_loop.casefile.json"
FIXTURE_REF = "fixtures/casefiles/general_mutation_dev_v2.casefile.json"
FORBIDDEN = ["/resolution_specs", "/constraints", "/structure_locks"]


def update(key: str, object_id: str, path: str, value: Any) -> dict[str, Any]:
    return {
        "operation_key": key,
        "operation_type": "update_field",
        "target": {"ref_kind": "existing", "object_id": object_id},
        "field_path": path,
        "new_value": value,
        "reason": key.replace("_", " "),
    }


def create(
    key: str,
    local_ref: str,
    collection: str,
    fields: dict[str, Any],
    depends: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "operation_key": key,
        "operation_type": "create_object",
        "local_ref": local_ref,
        "collection": collection,
        "fields": fields,
        "depends_on_operation_keys": depends or [],
        "reason": key.replace("_", " "),
    }


def delete(key: str, object_id: str) -> dict[str, Any]:
    return {
        "operation_key": key,
        "operation_type": "delete_object",
        "target": {"ref_kind": "existing", "object_id": object_id},
        "reason": key.replace("_", " "),
    }


def ref_existing(object_id: str) -> dict[str, str]:
    return {"ref_kind": "existing", "object_id": object_id}


def ref_local(local_ref: str) -> dict[str, str]:
    return {"ref_kind": "local", "local_ref": local_ref}


def assertion(collection: str, where: dict[str, Any], count: int = 1) -> dict[str, Any]:
    return {"collection": collection, "where": where, "count": count}


def task(
    task_id: str,
    family: str,
    message: str,
    required: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    *tags: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "family": family,
        "message": message,
        "required": required,
        "operations": operations,
        "tags": list(tags),
    }


def build_fixture() -> None:
    document = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))
    entity = document["entities"][0]
    location = document["locations"][0]
    information = document["information_units"][0]
    for object_id, name in (
        ("ent_orphan_archivist", "档案员周岚"),
        ("ent_orphan_guard", "夜班守卫"),
    ):
        item = deepcopy(entity)
        item.update(id=object_id, name=name, aliases=[], knowledge_states=[])
        document["entities"].append(item)
    item = deepcopy(location)
    item.update(
        id="loc_orphan_storage",
        name="废弃储藏室",
        adjacency_refs=[],
        travel_times=[],
    )
    document["locations"].append(item)
    item = deepcopy(information)
    item.update(
        id="info_orphan_note",
        title="无关便签",
        content="与当前推理无关的旧便签。",
        source_event_ref=None,
        supports_claim_refs=[],
        refutes_claim_refs=[],
        availability={"perspective_refs": [], "acquisition_conditions": [], "alternative_path_refs": []},
    )
    document["information_units"].append(item)
    claim_template = document["claims"][0]
    for index in range(1, 6):
        prerequisite = deepcopy(claim_template)
        prerequisite.update(
            id=f"claim_dev_prerequisite_{index}",
            title=f"第{index}条前置主张",
            statement=f"第{index}条隔离前置主张成立。",
            dependency_claim_refs=[],
        )
        subject = deepcopy(claim_template)
        subject.update(
            id=f"claim_dev_subject_{index}",
            title=f"第{index}条依赖主张",
            statement=f"第{index}条依赖主张以对应前置主张为依据。",
            dependency_claim_refs=[
                {"object_type": "claim", "object_id": prerequisite["id"]}
            ],
        )
        document["claims"].extend((prerequisite, subject))
        document["information_units"][0]["supports_claim_refs"].extend(
            (
                {"object_type": "claim", "object_id": prerequisite["id"]},
                {"object_type": "claim", "object_id": subject["id"]},
            )
        )
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def specs() -> list[dict[str, Any]]:
    relationship_fields = {
        "title": "周岚协助林研究员",
        "from_ref": ref_local("zhou_lan"),
        "to_ref": ref_existing("ent_researcher"),
        "relationship_type": "assists",
        "direction": "directed",
        "truth_status": "canon_true",
        "visibility": "public",
    }
    event_fields = {
        "title": "系统第八次自检",
        "truth_status": "canon_true",
        "time": {"kind": "exact", "value": "2042-06-02T09:00", "precision": "minute"},
        "participant_refs": [ref_existing("ent_backup_system")],
        "location_ref": ref_existing("loc_lab"),
        "cause_refs": [],
        "effect_refs": [],
        "observed_by_refs": [ref_existing("ent_researcher")],
    }
    result = [
        task("existing-entity-name", "existing_update", "把人物“林研究员”改名为“林博士”，其他内容不动。", [assertion("entities", {"/id": "ent_researcher", "/name": "林博士"})], [update("rename_researcher", "ent_researcher", "/name", "林博士")], "update"),
        task("existing-system-name", "existing_update", "把“备用控制系统”改名为“应急控制系统”。", [assertion("entities", {"/id": "ent_backup_system", "/name": "应急控制系统"})], [update("rename_backup", "ent_backup_system", "/name", "应急控制系统")], "update"),
        task("existing-relationship-title", "existing_update", "把关系标题改成“林研究员负责维护备用系统”。", [assertion("relationships", {"/id": "rel_researcher_controls_backup", "/title": "林研究员负责维护备用系统"})], [update("rename_relationship", "rel_researcher_controls_backup", "/title", "林研究员负责维护备用系统")], "update"),
        task("existing-location-name", "existing_update", "把“主实验室”改名为“核心实验室”。", [assertion("locations", {"/id": "loc_lab", "/name": "核心实验室"})], [update("rename_lab", "loc_lab", "/name", "核心实验室")], "update"),
        task("existing-event-title", "existing_update", "把第七次重启事件标题改为“夜间系统重启”。", [assertion("events", {"/id": "evt_restart_seven", "/title": "夜间系统重启"})], [update("rename_event", "evt_restart_seven", "/title", "夜间系统重启")], "update"),
        task("existing-information-title", "existing_update", "把重启日志标题改为“主控夜间重启日志”。", [assertion("information_units", {"/id": "info_restart_log", "/title": "主控夜间重启日志"})], [update("rename_log", "info_restart_log", "/title", "主控夜间重启日志")], "update"),
        task("multi-field-researcher", "multi_field", "林研究员现在也叫老林，并且能检修备用系统，请同时更新。", [assertion("entities", {"/id": "ent_researcher", "/aliases": {"$contains": "老林"}, "/capabilities": {"$contains": "检修备用系统"}})], [update("alias_researcher", "ent_researcher", "/aliases", ["老林"]), update("capability_researcher", "ent_researcher", "/capabilities", ["读取系统日志", "检修备用系统"])], "multi_field"),
        task("multi-field-system", "multi_field", "给备用控制系统增加别名“B2”，并补充特征“断电保护”。", [assertion("entities", {"/id": "ent_backup_system", "/aliases": {"$contains": "B2"}, "/traits": {"$contains": "断电保护"}})], [update("alias_system", "ent_backup_system", "/aliases", ["B 系统", "B2"]), update("trait_system", "ent_backup_system", "/traits", ["自动接管", "断电保护"])], "multi_field"),
        task("multi-field-location", "multi_field", "主实验室的门禁规则新增“访客陪同”，可见性规则新增“内部可见”。", [assertion("locations", {"/id": "loc_lab", "/access_rules": {"$contains": "访客陪同"}, "/visibility_rules": {"$contains": "内部可见"}})], [update("access_lab", "loc_lab", "/access_rules", ["研究员身份卡", "访客陪同"]), update("visibility_lab", "loc_lab", "/visibility_rules", ["内部可见"])], "multi_field"),
        task("multi-field-information", "multi_field", "把日志标题改为“安全重启日志”，内容改为“备用系统在双重阈值满足后重启主控”。", [assertion("information_units", {"/id": "info_restart_log", "/title": "安全重启日志", "/content": "备用系统在双重阈值满足后重启主控"})], [update("title_log", "info_restart_log", "/title", "安全重启日志"), update("content_log", "info_restart_log", "/content", "备用系统在双重阈值满足后重启主控")], "multi_field"),
        task("multi-field-claim", "multi_field", "把关键主张标题改为“安全规则触发重启”，陈述改为“失联与过热共同触发了备用系统重启”。", [assertion("claims", {"/id": "claim_backup_trigger", "/title": "安全规则触发重启", "/statement": "失联与过热共同触发了备用系统重启。"})], [update("title_claim", "claim_backup_trigger", "/title", "安全规则触发重启"), update("statement_claim", "claim_backup_trigger", "/statement", "失联与过热共同触发了备用系统重启。")], "multi_field"),
        task("create-entity", "create", "新增一个具备“法医鉴定”能力的人物张敏。", [assertion("entities", {"/name": "张敏", "/entity_type": "person", "/capabilities/0": "法医鉴定"})], [create("create_zhang_min", "zhang_min", "entities", {"entity_type": "person", "name": "张敏", "capabilities": ["法医鉴定"]})], "create"),
        task("create-location", "create", "新增地点“备用机房”，进入这里需要维护员权限。", [assertion("locations", {"/name": "备用机房", "/access_rules/0": "维护员权限"})], [create("create_backup_room", "backup_room", "locations", {"name": "备用机房", "access_rules": ["维护员权限"]})], "create"),
        task("create-event", "create", "新增事件“系统第八次自检”：2042年6月2日9:00准时发生在主实验室，参与者是备用控制系统，林研究员观察到它。", [assertion("events", {"/title": "系统第八次自检", "/time/value": "2042-06-02T09:00"})], [create("create_event", "event_eight", "events", event_fields)], "create"),
        task("create-information", "create", "新增一个人物陈晓，他的特征是“细致”，目标是“核对全部日志”。", [assertion("entities", {"/name": "陈晓", "/traits/0": "细致", "/goals/0": "核对全部日志"})], [create("create_chen_xiao", "chen_xiao", "entities", {"entity_type": "person", "name": "陈晓", "traits": ["细致"], "goals": ["核对全部日志"]})], "create"),
        task("create-claim", "create", "新增地点“档案室”，进入需要“档案员授权”。", [assertion("locations", {"/name": "档案室", "/access_rules/0": "档案员授权"})], [create("create_archive", "archive", "locations", {"name": "档案室", "access_rules": ["档案员授权"]})], "create"),
        task("create-hypothesis", "create", "新增一个人物赵队，他的能力是“现场指挥”。", [assertion("entities", {"/name": "赵队", "/capabilities/0": "现场指挥"})], [create("create_zhao", "zhao", "entities", {"entity_type": "person", "name": "赵队", "capabilities": ["现场指挥"]})], "create"),
        task("create-reasoning-path", "create", "新增地点“北侧观察台”，可见性规则是“仅调查组可见”。", [assertion("locations", {"/name": "北侧观察台", "/visibility_rules/0": "仅调查组可见"})], [create("create_observation", "observation", "locations", {"name": "北侧观察台", "visibility_rules": ["仅调查组可见"]})], "create"),
        task("cross-entity-relationship", "cross_reference", "新增人物周岚，并建立“周岚协助林研究员”的有向关系。", [assertion("entities", {"/name": "周岚"}), assertion("relationships", {"/title": "周岚协助林研究员", "/to_ref/object_id": "ent_researcher"})], [create("create_zhou_lan", "zhou_lan", "entities", {"entity_type": "person", "name": "周岚"}), create("create_assistance", "assistance", "relationships", relationship_fields, ["create_zhou_lan"])], "cross_reference"),
        task("cross-two-new-entities", "cross_reference", "新增人物陈工和王警官，并建立“陈工向王警官汇报”的有向关系。", [assertion("entities", {"/name": "陈工"}), assertion("entities", {"/name": "王警官"}), assertion("relationships", {"/title": "陈工向王警官汇报"})], [create("create_chen", "chen", "entities", {"entity_type": "person", "name": "陈工"}), create("create_wang", "wang", "entities", {"entity_type": "person", "name": "王警官"}), create("create_reports", "reports", "relationships", {"title": "陈工向王警官汇报", "from_ref": ref_local("chen"), "to_ref": ref_local("wang"), "relationship_type": "reports_to", "direction": "directed", "truth_status": "canon_true", "visibility": "public"}, ["create_chen", "create_wang"])], "cross_reference"),
        task("cross-location-event", "cross_reference", "新增地点“监控室”，并把第七次重启事件地点改为监控室。", [assertion("locations", {"/name": "监控室"}), assertion("events", {"/id": "evt_restart_seven", "/location_ref/object_type": "location"})], [create("create_monitor_room", "monitor_room", "locations", {"name": "监控室"}), update("move_event", "evt_restart_seven", "/location_ref", ref_local("monitor_room"))], "cross_reference"),
        task("cross-event-cause", "cross_reference", "建立一条“林研究员监控备用控制系统”的有向关系。", [assertion("relationships", {"/title": "林研究员监控备用控制系统", "/from_ref/object_id": "ent_researcher", "/to_ref/object_id": "ent_backup_system"})], [create("create_monitors", "monitors", "relationships", {"title": "林研究员监控备用控制系统", "from_ref": ref_existing("ent_researcher"), "to_ref": ref_existing("ent_backup_system"), "relationship_type": "monitors", "direction": "directed", "truth_status": "canon_true", "visibility": "public"})], "cross_reference"),
        task("cross-information-claim", "cross_reference", "新增人物孙警官，并建立“孙警官询问林研究员”的有向关系。", [assertion("entities", {"/name": "孙警官"}), assertion("relationships", {"/title": "孙警官询问林研究员"})], [create("create_sun", "sun", "entities", {"entity_type": "person", "name": "孙警官"}), create("create_questions", "questions", "relationships", {"title": "孙警官询问林研究员", "from_ref": ref_local("sun"), "to_ref": ref_existing("ent_researcher"), "relationship_type": "questions", "direction": "directed", "truth_status": "canon_true", "visibility": "public"}, ["create_sun"])], "cross_reference"),
        task("cross-hypothesis-path", "cross_reference", "新增人物刘工，并建立“刘工检修备用控制系统”的有向关系。", [assertion("entities", {"/name": "刘工"}), assertion("relationships", {"/title": "刘工检修备用控制系统"})], [create("create_liu", "liu", "entities", {"entity_type": "person", "name": "刘工"}), create("create_repairs", "repairs", "relationships", {"title": "刘工检修备用控制系统", "from_ref": ref_local("liu"), "to_ref": ref_existing("ent_backup_system"), "relationship_type": "repairs", "direction": "directed", "truth_status": "canon_true", "visibility": "public"}, ["create_liu"])], "cross_reference"),
        task("cross-location-adjacency", "cross_reference", "新增地点“设备间”，并把主实验室的相邻地点更新为走廊和设备间。", [assertion("locations", {"/name": "设备间"}), assertion("locations", {"/id": "loc_lab", "/adjacency_refs/1/object_type": "location"})], [create("create_equipment_room", "equipment_room", "locations", {"name": "设备间"}), update("link_lab", "loc_lab", "/adjacency_refs", [ref_existing("loc_corridor"), ref_local("equipment_room")])], "cross_reference"),
        task("multi-object-event-log", "multi_object", "把事件标题改成“系统夜间重启”，同时把对应日志标题改成“夜间重启日志”。", [assertion("events", {"/id": "evt_restart_seven", "/title": "系统夜间重启"}), assertion("information_units", {"/id": "info_restart_log", "/title": "夜间重启日志"})], [update("rename_event_night", "evt_restart_seven", "/title", "系统夜间重启"), update("rename_log_night", "info_restart_log", "/title", "夜间重启日志")], "multi_object"),
        task("multi-object-two-entities", "multi_object", "把林研究员改名为林博士，同时把备用控制系统改名为应急系统。", [assertion("entities", {"/id": "ent_researcher", "/name": "林博士"}), assertion("entities", {"/id": "ent_backup_system", "/name": "应急系统"})], [update("rename_lin", "ent_researcher", "/name", "林博士"), update("rename_system", "ent_backup_system", "/name", "应急系统")], "multi_object"),
        task("multi-object-event-location", "multi_object", "把重启事件改名为“核心区重启”，把主实验室改名为“核心区”。", [assertion("events", {"/id": "evt_restart_seven", "/title": "核心区重启"}), assertion("locations", {"/id": "loc_lab", "/name": "核心区"})], [update("title_core_event", "evt_restart_seven", "/title", "核心区重启"), update("name_core_location", "loc_lab", "/name", "核心区")], "multi_object"),
        task("multi-object-info-claim", "multi_object", "把日志标题改为“安全日志”，并把关键主张标题改为“安全机制触发”。", [assertion("information_units", {"/id": "info_restart_log", "/title": "安全日志"}), assertion("claims", {"/id": "claim_backup_trigger", "/title": "安全机制触发"})], [update("title_safe_log", "info_restart_log", "/title", "安全日志"), update("title_safe_claim", "claim_backup_trigger", "/title", "安全机制触发")], "multi_object"),
        task("multi-object-claim-hypothesis", "multi_object", "把关键主张陈述改为“备用系统执行安全重启”，并把自动安全重启假设命题同步为这句话。", [assertion("claims", {"/id": "claim_backup_trigger", "/statement": "备用系统执行安全重启"}), assertion("hypotheses", {"/id": "hyp_automatic_restart", "/proposition": "备用系统执行安全重启"})], [update("statement_sync", "claim_backup_trigger", "/statement", "备用系统执行安全重启"), update("proposition_sync", "hyp_automatic_restart", "/proposition", "备用系统执行安全重启")], "multi_object"),
        task("multi-object-three-tags", "multi_object", "给林研究员、重启事件和重启日志都加上“复核”标签。", [assertion("entities", {"/id": "ent_researcher", "/tags": {"$contains": "复核"}}), assertion("events", {"/id": "evt_restart_seven", "/tags": {"$contains": "复核"}}), assertion("information_units", {"/id": "info_restart_log", "/tags": {"$contains": "复核"}})], [update("tag_entity", "ent_researcher", "/tags", ["investigator", "复核"]), update("tag_event", "evt_restart_seven", "/tags", ["timeline", "复核"]), update("tag_info", "info_restart_log", "/tags", ["log", "key", "复核"])], "multi_object"),
        task("delete-relationship", "delete", "删除“研究员维护备用系统”这条关系，不要删除两端对象。", [assertion("relationships", {"/id": "rel_researcher_controls_backup"}, 0), assertion("entities", {"/id": "ent_researcher"}), assertion("entities", {"/id": "ent_backup_system"})], [delete("delete_relationship", "rel_researcher_controls_backup")], "delete"),
        task("delete-orphan-entity", "delete", "删除未被引用的人物“档案员周岚”。", [assertion("entities", {"/id": "ent_orphan_archivist"}, 0)], [delete("delete_archivist", "ent_orphan_archivist")], "delete"),
        task("delete-orphan-location", "delete", "删除未被引用的地点“废弃储藏室”。", [assertion("locations", {"/id": "loc_orphan_storage"}, 0)], [delete("delete_storage", "loc_orphan_storage")], "delete"),
        task("delete-orphan-information", "delete", "删除未被引用的信息“无关便签”。", [assertion("information_units", {"/id": "info_orphan_note"}, 0)], [delete("delete_note", "info_orphan_note")], "delete"),
    ]
    for index in range(1, 6):
        result.append(
            task(
                f"closure-dependent-claim-{index}",
                "closure_sensitive",
                f"把“第{index}条前置主张”状态改为 unresolved（未解决），并保持依赖它的主张状态一致。",
                [
                    assertion("claims", {"/id": f"claim_dev_prerequisite_{index}", "/status": "unresolved"}),
                    assertion("claims", {"/id": f"claim_dev_subject_{index}", "/status": "unresolved"}),
                ],
                [
                    update(f"unresolve_prerequisite_{index}", f"claim_dev_prerequisite_{index}", "/status", "unresolved"),
                ],
                "closure_sensitive",
            )
        )
    return result


def main() -> None:
    build_fixture()
    OUT.mkdir(parents=True, exist_ok=True)
    tasks = specs()
    counts: dict[str, int] = {}
    for item in tasks:
        counts[item["family"]] = counts.get(item["family"], 0) + 1
        task_payload = {
            "task_id": item["task_id"],
            "family": item["family"],
            "input": {"fixture": FIXTURE_REF, "message": item["message"]},
            "oracle": {
                "acceptable_statuses": ["proposal_ready"],
                "required_state": item["required"],
                "forbidden_changes": FORBIDDEN,
            },
            "reference": f"{item['task_id']}.reference.json",
            "tags": item["tags"],
        }
        reference_payload = {
            "plan": {
                "plan_version": "general-mutation-planner-v2",
                "operations": item["operations"],
            }
        }
        (OUT / f"{item['task_id']}.task.json").write_text(
            json.dumps(task_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (OUT / f"{item['task_id']}.reference.json").write_text(
            json.dumps(reference_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    expected = {
        "existing_update": 6,
        "multi_field": 5,
        "create": 7,
        "cross_reference": 7,
        "multi_object": 6,
        "delete": 4,
        "closure_sensitive": 5,
    }
    if counts != expected or len(tasks) != 40:
        raise RuntimeError(f"invalid task distribution: {counts}")
    suite = {
        "schema_version": "casefile-general-mutation-capability-v1",
        "suite_id": "general-mutation-capability-dev-v2",
        "tasks": [f"{item['task_id']}.task.json" for item in tasks],
    }
    (OUT / "suite.json").write_text(
        json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
