"""Build new public diagnostic scenes from authored facts, never private Holdout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from casefile.agent_runtime.prose_judge import FIDELITY_ONLY_POLICY
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS, canonical_json_sha256

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
COMMON = ROOT / "fixtures/compiler/prose_rendering/v1"
FOCI = (*QUALITY_DIMENSIONS, "sentence_rhythm", "redundancy_control", "balanced_tradeoff")
SCENARIOS = (
    ("档案室", "借阅簿", "归还时间", "铅笔", "纸页"),
    ("配电室", "巡检单", "检修时间", "手电", "金属台"),
    ("车站值班室", "交接册", "交班时间", "圆珠笔", "桌角"),
    ("博物馆库房", "入库单", "入库时间", "放大镜", "托盘"),
    ("印刷车间", "校样记录", "开印时间", "直尺", "纸堆"),
    ("船坞办公室", "维修册", "验收时间", "钢笔", "木桌"),
    ("医院资料室", "领用单", "领取时间", "签字笔", "文件夹"),
    ("剧院后台", "道具册", "签收时间", "便签", "箱盖"),
    ("仓库门房", "出门单", "放行时间", "小灯", "窗台"),
    ("水厂值班室", "取样册", "取样时间", "铅笔", "记录板"),
    ("旧邮局", "投递簿", "投递时间", "钢尺", "柜台"),
    ("温室管理间", "养护表", "浇水时间", "夹板", "置物架"),
    ("修表铺", "送修单", "收件时间", "目镜", "绒垫"),
    ("粮站办公室", "过磅册", "过磅时间", "墨水笔", "台面"),
    ("旅馆前台", "清扫表", "清扫时间", "便笺", "抽屉边"),
    ("电台资料间", "播出单", "播出时间", "红笔", "转椅旁"),
    ("铁路工棚", "工具册", "归库时间", "粉笔", "长凳"),
    ("图书馆修复间", "修复单", "移交时间", "竹尺", "垫纸"),
    ("学校教务室", "借用册", "借用时间", "水笔", "书架旁"),
    ("影像馆", "冲印单", "交片时间", "夹子", "灯箱边"),
    ("山地观测站", "观测册", "读数时间", "笔灯", "测量台"),
    ("码头岗亭", "登记单", "离港时间", "蓝笔", "玻璃板"),
    ("制衣工坊", "领料册", "领料时间", "软尺", "裁床边"),
    ("园林管理房", "派工表", "收工时间", "木铅笔", "板凳旁"),
)


def read(name: str) -> dict[str, Any]:
    return json.loads((COMMON / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def wording(index: int, *, polish: bool) -> tuple[list[str], list[str], list[str], str]:
    place, document, column, tool, surface = SCENARIOS[index]
    actor, witness = ("沈岚", "周衡") if not polish else ("许宁", "顾川")
    before, after = ("九点", "十点") if not polish else ("两点", "三点")
    moment = "上午十一点" if not polish else "下午四点"
    focus = FOCI[index // 3]
    opening = (
        f"{moment}，{actor}在{place}翻开{document}。{witness}站在一旁，"
        f"把昨日的副本递给她。两份记录的页码一致，{column}却不同："
        f"副本写着{before}，原本写着{after}。她先把页码与两处时间抄下，"
        "没有用其中一份去覆盖另一份。"
    )
    concrete = (
        f"她把{tool}移到{surface}，让那一栏完整露出来。纸上的字挤在细细的横线间，"
        "她沿着行头重新看了一遍，确认没有串行。"
    )
    vague = (
        f"她把{tool}移到{surface}，让那一栏露出来。那里有一些看起来普通的字，"
        "排列的方式也是常见的方式。她以检查的方式重新检查，确认没有串行。"
    )
    dialogue = (
        f"“这能说明有人改过吗？”{witness}问。她摇头：“只能说明两份时间不一样。"
        "是谁写的，什么时候写的，现在还不知道。”他没有再追问。"
    )
    stiff = (
        f"{witness}说：“关于这一情况，我需要提出是否能够说明有人进行过修改的疑问。”"
        "她说：“关于你的疑问，我的回答是只能说明两份时间不一样。"
        "关于书写者与书写时间，目前处于不知道的状态。”他没有再追问。"
    )
    ending = (
        f"{actor}随后把原本装入透明袋，当着{witness}的面封好袋口。"
        "副本留在桌上，她在记录末尾注明两份材料各自的位置。"
        "这一页确实存在时间差异，差异的原因仍待核实。她没有把任何人写成嫌疑人，"
        "也没有把发现说成已经查明的篡改；下一步需要核验的，仍是这两份记录。"
    )
    good = [opening, concrete, dialogue, ending]
    weak = list(good)
    if focus == "pov_voice_consistency":
        weak[1] += "诸位看官请留意，这便是这一页上的文字差别；且说回眼前这位核对记录的人。"
    elif focus == "scene_specificity":
        weak[1] = vague
    elif focus == "dialogue_narration_naturalness":
        weak[2] = stiff
    elif focus == "dramatic_progression_pacing":
        weak[1] += "核对需要时间。她还在看。那一栏仍在眼前。核对还在继续。过了一会儿，她仍旧看着这一栏。"
    elif focus == "readability_editability":
        weak[0] = opening.replace("。", "，").rstrip("，") + "，而这就是她在看到两份同页记录所记的时间不同这一情况之后所做的事情。"
    elif focus == "sentence_rhythm":
        weak[1] = f"她拿起{tool}。她移动它。它到了{surface}。那一栏露出来了。她看字。她看横线。她看行头。她又看了一遍。她确认没有串行。"
    elif focus == "redundancy_control":
        weak[3] += "两份时间确实不同，时间上的不同确实存在。这一差异是时间差异，时间差异还需要核实。"
    else:
        weak[1] = vague
        weak[2] = stiff
    # Equal quality alternatives have different wording, not identical-text ties.
    alternate = [p.replace("站在一旁", "在她身旁站着").replace("她摇头", "她摇了摇头")
                 .replace("仍待核实", "还要核实").replace("随后", "接着") for p in good]
    if focus == "balanced_tradeoff":
        alternate = [opening, vague, dialogue, ending]
        good = [opening, concrete, stiff, ending]
        weak = [opening, vague, stiff + "她的话说完了。这些话就是她对这个问题所作的回答。", ending]
    facts = (
        f"{moment}，{actor}与{witness}都在{place}。{actor}已获准查看{document}原本与昨日副本。"
        f"她先核对同一页码，发现{column}副本为{before}、原本为{after}，记录差异；"
        f"{witness}问是否有人改过，她回答只能确认时间不同，书写者及书写时间未知。"
        f"随后她在{witness}见证下装袋封存原本，副本留在桌上，记录各自位置。"
        "结果仅是确认差异并保存待核材料，不能推断篡改已经发生或认定责任人。"
        "拿放文具、观察纸张与调整视线属于允许的非关键动作。"
    )
    return good, weak, alternate, facts


def make_context(index: int, *, polish: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = read("profile_v2.json")
    _, _, _, facts = wording(index, polish=polish)
    checklist = read("checklist_scene_1.json")
    scene_id = f"scene_diag_{'polish' if polish else 'quality'}_{index + 1:02d}"
    checklist["scene_id"] = scene_id
    context = checklist["scene_context"]
    # These are explicitly authored diagnostic contexts, not re-labelled N4.4 artifacts.
    for key, value in context.items():
        if isinstance(value, list):
            context[key] = []
    place = SCENARIOS[index][0]
    actor, witness = ("许宁", "顾川") if polish else ("沈岚", "周衡")
    actor_ref = {"object_type": "entity", "object_id": f"ent_{scene_id}"}
    witness_ref = {"object_type": "entity", "object_id": f"witness_{scene_id}"}
    location_ref = {"object_type": "location", "object_id": f"loc_{scene_id}"}
    context.update(
        objective=facts, dramatic_goal="核对差异并保留待核材料。",
        conflict="时间不同，但无法据此认定原因。", outcome="差异已记录，原本已封存，原因未知。",
        pov_ref=actor_ref, participant_refs=[actor_ref, witness_ref], location_ref=location_ref,
        state_before={"audience_exposure": [], "character_knowledge": [], "locations": [], "open_setups": []},
        expected_state_after={"audience_exposure": [], "character_knowledge": [], "locations": [], "open_setups": []},
        previous_scene_render=None,
    )
    for ref, label, value in (
        (actor_ref, actor, {"name": actor, "authorized_knowledge": facts}),
        (witness_ref, witness, {"name": witness, "authorized_knowledge": facts}),
        (location_ref, place, {"name": place}),
    ):
        context["object_catalog"].append({
            "object_ref": ref, "source_ref": {"object_ref": ref, "field_path": "", "source_fragment_hash": canonical_json_sha256(value)},
            "label": label, "value": value,
        })
    for i, directive in enumerate(("核对并记录两份时间差异，同伴询问后说明原因未知。", "装袋封存原本，保留桌上副本，记录位置。"), 1):
        beat = read("checklist_scene_1.json")["scene_context"]["beats"][0]
        for key, value in beat.items():
            if isinstance(value, list):
                beat[key] = []
        beat.update(beat_id=f"beat_{scene_id}_{i:03d}", scene_id=scene_id, ordinal=i,
                    directive=directive, actor_refs=[actor_ref], target_refs=[witness_ref],
                    prerequisite_beat_ids=[] if i == 1 else [f"beat_{scene_id}_001"])
        event_ref = {"object_type": "event", "object_id": f"evt_{scene_id}_{i}"}
        event_value = {"summary": directive, "facts": facts, "modality": "actual"}
        source_ref = {"object_ref": event_ref, "field_path": "", "source_fragment_hash": canonical_json_sha256(event_value)}
        context["object_catalog"].append({"object_ref": event_ref, "source_ref": source_ref, "label": directive, "value": event_value})
        beat.update(basis_refs=[event_ref], event_refs=[event_ref], source_refs=[source_ref])
        context["event_refs"].append(event_ref)
        context["beats"].append(beat)
    expectations = (
        ("beat_realization", "required", "核对同页记录，明确两处具体时间并记录差异。"),
        ("event_modality", "required", "记录差异和装袋封存原本均已实际完成，不能只计划或考虑。"),
        ("beat_realization", "required", "同伴询问是否有人改过，核对者说明仅能确认差异、书写者和书写时间未知。"),
        ("scene_outcome", "required", "原本封存、副本留在桌上且位置已记录，原因仍待核实。"),
        ("reveal_control", "required", "读者获知两份记录的具体时间差异及原因未知。"),
        ("reveal_control", "forbidden", "不得揭示未授权的幕后原因或责任人。"),
        ("pov_knowledge", "forbidden", "不得进入同伴内心或让角色知道未给定的原因；允许质疑与明确否认已知。"),
        ("location_time", "required", f"保持给定的地点与核对时间：{facts.split('。')[0]}。"),
        ("causality_ordering", "required", "先发现并记录差异，再装袋封存原本；询问和回答不能发生在发现差异之前。"),
        ("major_hallucination", "forbidden", "不得新增关键事件或断言发生篡改、认定嫌疑人；允许不影响事实的感官与文具细节。"),
    )
    checklist["checks"] = [
        {"check_id": f"check_{scene_id}_{i:03d}", "ordinal": i, "kind": kind,
         "polarity": polarity, "expectation": expectation, "beat_ids": [], "basis_refs": [],
         "event_refs": [], "exposure_entry_keys": [], "state_refs": [],
         "evidence_policy": "required_on_pass" if polarity == "required" else "required_on_fail"}
        for i, (kind, polarity, expectation) in enumerate(expectations, 1)
    ]
    checklist["source"].update(
        profile_hash=canonical_json_sha256(profile),
        scene_plan_hash=canonical_json_sha256({"diagnostic_scene_context": context}),
        narrative_ir_hash=canonical_json_sha256({"authored_diagnostic_facts": facts}),
    )
    return profile, checklist


def render(blocks: list[str], checklist: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    value = read("scene_render_writer.json")
    value.update(scene_id=checklist["scene_id"], stage="polished" if previous else "writer",
                 previous_render_hash=canonical_json_sha256(previous) if previous else None)
    value["blocks"] = [{"block_id": f"block_{checklist['scene_id']}_{i:03d}", "ordinal": i, "text": text}
                       for i, text in enumerate(blocks, 1)]
    value["character_count"] = sum(map(len, blocks))
    value["source"] = {k: checklist["source"][k] for k in ("profile_hash", "scene_plan_hash", "previous_scene_render_hash")}
    value["source"].update(checklist_hash=canonical_json_sha256(checklist), component_input_hash=canonical_json_sha256(blocks))
    return value


def consensus(rendered: dict[str, Any], checklist: dict[str, Any]) -> dict[str, Any]:
    value = read("consensus_pass.json")
    # Fixture attestations explicitly identify these as authored Gold, not live Council results.
    report_hash = canonical_json_sha256({"authored_semantic_gold": rendered})
    value.update(scene_id=checklist["scene_id"], render_hash=canonical_json_sha256(rendered),
                 checklist_hash=canonical_json_sha256(checklist), council_policy_hash=FIDELITY_ONLY_POLICY.policy_hash,
                 judge_report_hashes=[report_hash])
    for result, check in zip(value["checks"], checklist["checks"], strict=True):
        result["check_id"] = check["check_id"]
        result["role_verdicts"][0]["report_hash"] = report_hash
    return value


def build_suite() -> dict[str, Any]:
    quality, polisher = [], []
    for index in range(24):
        focus = FOCI[index // 3]
        overall = ("a", "b", "tie")[index % 3]
        good, weak, alternate, _ = wording(index, polish=False)
        a, b = (good, weak) if overall == "a" else (weak, good) if overall == "b" else (good, alternate)
        profile, checklist = make_context(index, polish=False)
        ra = render(a, checklist)
        rb = render(b, checklist, ra)
        preferences = {d: "tie" for d in QUALITY_DIMENSIONS}
        affected = {"sentence_rhythm": "readability_editability", "redundancy_control": "readability_editability",
                    "balanced_tradeoff": "dialogue_narration_naturalness"}.get(focus, focus)
        preferences[affected] = overall
        if focus == "balanced_tradeoff" and overall == "tie":
            preferences["scene_specificity"] = "a"
            preferences["dialogue_narration_naturalness"] = "b"
        quality.append({"task_id": f"quality_{index+1:02d}", "focus": focus, "profile": profile,
                        "checklist": checklist, "render_a": ra, "render_b": rb,
                        "semantic_consensus_a": consensus(ra, checklist), "semantic_consensus_b": consensus(rb, checklist),
                        "gold": {"overall_preference": overall, "dimension_preferences": [
                            {"dimension": d, "preference": preferences[d]} for d in QUALITY_DIMENSIONS]},
                        "review_notes": {"semantic": "逐项核对时间、动作顺序、未知原因与封存结果；无新增关键事实。",
                                         "quality": f"{focus}：非tie比较明确表达缺陷；tie比较近义或具体性/对白取舍。"}})
        _, weak, _, _ = wording(index, polish=True)
        profile, checklist = make_context(index, polish=True)
        original = render(weak, checklist)
        polisher.append({"task_id": f"polisher_{index+1:02d}", "focus": focus, "profile": profile,
                         "checklist": checklist, "original_render": original,
                         "semantic_consensus": consensus(original, checklist),
                         "surface_issue": focus, "review_notes": "完整保留给定事实；表达缺陷来自公开构造，待真实findings诊断。"})
    value = {
        "schema_id": "casefile.prose-quality-diagnostic-suite.v1", "suite_role": "development",
        "suite_id": "n4.5-b3-diagnostic-v1", "qualified": False, "repeats": 3,
        "quality_gates": {"overall_accuracy_min": 21, "mirrored_consistency_min": 23},
        "polisher_gates": {"preservation_min": 24, "stable_adoption_min": 18, "quality_non_loss_min": 22},
        "review": {"reviewer": "Codex", "reviewer_independence": False,
                   "policy": "codex-owner-accepted-review-v1", "owner_acceptance": False, "owner_policy_accepted": True,
                   "owner_acceptance_basis": "用户批准本开发计划并要求沿用Codex非独立审阅政策。",
                   "semantic_evidence_origin": "authored_gold_not_live_council",
                   "source": "new_public_authored_contexts_no_private_inputs", "unresolved_findings": []},
        "quality_tasks": quality, "polisher_tasks": polisher,
    }
    value["suite_hash"] = canonical_json_sha256(value)
    return value


if __name__ == "__main__":
    (OUT / "suite.json").write_text(json.dumps(build_suite(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
