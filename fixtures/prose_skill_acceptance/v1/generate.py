"""Author a clean public acceptance fixture from readable source and structural skeleton.

This is a new development fixture, not a repair of any frozen historical suite.
Additional manual-trace facts are explicitly authored here; no private data is used.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from casefile.agent_runtime.providers import FakeProvider
from casefile.agent_runtime.scene_compiler import execute_scene_semantic_fill
from casefile.domain.narrative_compiler import (
    build_scene_compiler_input_v2,
    build_scene_compiler_model_view,
    canonical_json_sha256,
    compile_scene_plan_v2,
    project_narrative_ir_json,
)
from casefile_contracts import CaseFile, NarrativeIR

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
AUTHORED = {
    "res_root_cause/conclusion/summary": "第七次重启由备用系统依据安全规则自动触发。",
    "res_root_cause/conclusion/rationale": "日志表明主控失联与过热同时持续三分钟。",
    "ent_safety_observer/traits/0": "谨慎",
    "ent_safety_observer/goals/0": "核对系统安全记录",
    "ent_safety_observer/capabilities/0": "核对现场记录",
    "info_manual_trace/title": "操作台人工触发痕迹",
    "info_manual_trace/content": "操作台有一条人工触发痕迹，尚不能确定它属于哪一次重启。",
    "info_manual_trace/availability/acquisition_conditions/0": "查看操作台记录",
    "claim_manual_trigger/title": "人工触发假设",
    "claim_manual_trigger/statement": "重启可能由操作台的人工指令触发。",
    "hyp_automatic_restart/evidence_assessments/0/rationale": "日志支持备用系统自动保护。",
    "hyp_automatic_restart/evidence_assessments/1/rationale": "人工痕迹尚不足以推翻系统日志。",
    "hyp_manual_restart/title": "人工重启假设",
    "hyp_manual_restart/proposition": "有人从操作台发出了重启指令。",
    "hyp_manual_restart/evidence_assessments/0/rationale": "操作台痕迹可提示人工介入的可能。",
    "hyp_manual_restart/evidence_assessments/1/rationale": "日志支持自动保护，削弱人工假设。",
    "path_manual_restart/title": "从操作痕迹检查人工假设",
}
REFERENCES = [
    (
        "二十点整，主实验室的屏幕暗了一瞬，随后重新亮起。林研究员站在操作台前，"
        "看着备用控制系统完成第七次重启。他把手从键盘上移开，先记下时间，没有急着判断原因。"
        "房间里只有设备运转的声音，他靠近屏幕，将操作台记录翻到当前页。一条人工触发痕迹"
        "出现在列表里，短短一行，既没有给出足够的上下文，也无法说明它究竟属于哪一次重启。"
        "他把光标停在那里，重新读了一遍。有人发过指令，还是一条尚未对上的旧记录？现在还不能确认。"
        "他在纸上记下人工痕迹四个字，在旁边留出一块空白，没有写操作者的名字。"
        "系统已经恢复显示，操作台的边缘映着屏幕的光。他坐回椅子，将这条记录单独标出，"
        "准备在后续核对中寻找它的位置。他尚未打开系统重启日志，不知道其中记录了什么。"
        "桌面上那张纸只写着第七次重启的时间和一条待查痕迹。他把笔放在纸边，"
        "目光重新落回屏幕，决定先把已经看见的东西保存好。此刻能确定的是重启确实发生了，"
        "而那条人工痕迹的归属仍然没有答案。"
    ),
    (
        "林研究员把刚才记下的人工痕迹留在手边，调出第七次重启的系统日志。"
        "重启已经完成，终端重新可读。他逐行核对记录，看到主控失联和温度超限两项状态"
        "同时持续了三分钟，随后备用系统执行了强制重启。他没有把其中一项单独摘出来，"
        "而是把两项条件以及共同持续的时间写在同一行。先前那条人工痕迹仍在纸上，"
        "但它已经不能直接说明这次重启的原因。他往前翻了几行，又回到触发记录，"
        "确认自己没有把别的事件混进来。日志记录的是备用系统按安全规则采取的动作，"
        "不是一份已经查明操作者的人工命令。他把这条信息保存下来，在原先的问号旁添上"
        "一小段说明：人工痕迹尚待归属，第七次重启有自动保护的日志依据。"
        "写完后，他把纸放平，重新看了一遍三分钟的时间要求。两项条件必须同时存在，"
        "不能只凭失联或者过热就简化结论。屏幕上的记录没有改变，林研究员的判断却比"
        "刚才清楚了一些。他保留了原来的记录，也保留了刚读到的日志，准备继续核对。"
    ),
    (
        "林研究员重新打开第七次重启的日志，确认那次动作已经完成。他把主控失联、"
        "温度超限和两项状态共同持续三分钟的记录并排放在眼前，逐项核对之后，才写下结论。"
        "第七次重启由备用控制系统依据安全规则自动触发，不能直接归因于那条尚未定位的人工痕迹。"
        "这条痕迹并没有被删除。他在旁边补上说明，指出它目前不能证明本次重启存在人工操作。"
        "这样，先前的疑问就有了新的位置：痕迹可以继续查，已经查清的自动触发机制则应当保留。"
        "他又把安全规则单独写了一遍。失联与过热必须同时持续三分钟，两个条件和持续时间缺一不可。"
        "写到这里，他停下笔，从头检查自己有没有把某个假设写成已知事实。"
        "根因结论有系统日志支持，组合规则也能在同一份记录中核对，而人工痕迹的来源仍然未知。"
        "他将这三点分开记录，把日志与纸上的笔记放在一起。主实验室里的设备继续运行，"
        "林研究员没有再为未知的操作者添加名字，也没有猜测新的事件。他保存当前记录，"
        "让已经获得的结论和仍待调查的问题各自保持清楚。"
    ),
]


def readable(value: Any, original: Any, path: str) -> Any:
    if isinstance(value, dict):
        return {
            k: readable(
                v,
                original.get(k) if isinstance(original, dict) else None,
                f"{path}/{k}",
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            readable(
                v,
                original[i]
                if isinstance(original, list) and i < len(original)
                else None,
                f"{path}/{i}",
            )
            for i, v in enumerate(value)
        ]
    if isinstance(value, str) and "\ufffd" in value:
        if isinstance(original, str) and "\ufffd" not in original:
            return original
        return AUTHORED[path]
    return value


def build() -> dict[str, Any]:
    source_path = ROOT / "fixtures/casefiles/restart_loop.casefile.json"
    skeleton_path = (
        ROOT / "fixtures/scene_plan_benchmark/v1/inputs/dependency_transfer__basic.json"
    )
    source = json.loads(source_path.read_text(encoding="utf-8"))
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    document = deepcopy(source)
    for collection, envelopes in skeleton["narrative_ir"]["objects"].items():
        originals = {item["id"]: item for item in source[collection]}
        document[collection] = [
            readable(
                item["value"],
                originals.get(item["object_ref"]["object_id"]),
                item["object_ref"]["object_id"],
            )
            for item in envelopes
        ]
    document = CaseFile.model_validate(document).model_dump(mode="json")
    narrative = NarrativeIR.model_validate(
        project_narrative_ir_json(document)
    ).model_dump(mode="json")
    profile = json.loads(
        (ROOT / "fixtures/compiler/prose_rendering/v1/profile_v2.json").read_text(
            encoding="utf-8"
        )
    )
    profile["prose"]["target_scene_chars"] = {"min": 300, "max": 1200}
    plan = deepcopy(skeleton["novel_plan"])

    # Author a linear three-step reveal scenario. The historical skeleton's
    # flashback would otherwise expose the seventh log during the sixth restart.
    def seventh(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: seventh(v) for k, v in value.items()}
        if isinstance(value, list):
            return [seventh(v) for v in value]
        return "evt_restart_seven" if value == "evt_restart_six" else value

    plan["scenes"][1] = seventh(plan["scenes"][1])
    plan["scenes"][1]["presentation_mode"] = "linear"
    intents = [
        "观察第七次重启并发现尚未归属的人工痕迹；暂不揭示系统日志。",
        "承接上场，读取第七次重启日志，核对自动保护的组合触发条件。",
        "依据日志确认根因与组合规则，重新解释人工痕迹，形成调查结论。",
    ]
    for scene, intent in zip(plan["scenes"], intents, strict=True):
        scene["intent"] = intent
    plan["source"].update(
        narrative_ir_hash=canonical_json_sha256(narrative),
        profile_hash=canonical_json_sha256(profile),
        exposure_hash=None,
    )
    bundle = build_scene_compiler_input_v2(
        novel_plan=plan,
        narrative_ir=narrative,
        exposure=None,
        profile={
            "profile_key": "skill-acceptance",
            "profile_schema_id": "compiler.novel-profile.v2",
            "profile_version": 1,
            "frozen_payload": profile,
            "content_hash": canonical_json_sha256(profile),
        },
    )
    view = build_scene_compiler_model_view(bundle)
    fills = execute_scene_semantic_fill(
        FakeProvider(),
        task_run_id=1,
        model_view=view,
        component_hash=canonical_json_sha256(bundle),
        model_id="fixture-authoring",
        api_key="unused",
    )
    proposals = deepcopy(list(fills.proposals))
    for scene, intent in zip(proposals[0]["scenes"], intents, strict=True):
        scene["dramatic_goal"] = intent
        scene["conflict"] = "人工痕迹可能造成误判，必须按当前允许的信息范围核对事实。"
        scene["outcome"] = intent
    scene_plan = compile_scene_plan_v2(
        scene_compiler_input=bundle, semantic_fills=proposals
    )
    result = {
        "suite_role": "development",
        "qualified": False,
        "authorship": "Readable public source plus explicitly authored supplemental facts; structural fake fill only",
        "sources": {
            str(source_path.relative_to(ROOT)): canonical_json_sha256(source),
            str(skeleton_path.relative_to(ROOT)): canonical_json_sha256(skeleton),
        },
        "authored_text": AUTHORED,
        "document": document,
        "narrative_ir": narrative,
        "profile": profile,
        "scene_compiler_input": bundle,
        "semantic_fills": proposals,
        "scene_plan": scene_plan,
        "reference_texts": REFERENCES,
    }
    if "\ufffd" in json.dumps(result, ensure_ascii=False):
        raise ValueError("Acceptance source contains damaged text")
    result["content_hash"] = canonical_json_sha256(result)
    return result


if __name__ == "__main__":
    (OUT / "suite.json").write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
