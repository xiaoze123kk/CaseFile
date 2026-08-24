"""Immutable, packaged System Prompt Repository contract tests."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from casefile.agent_runtime.prompt import (
    AGENT_VERSION,
    CHAT_PROMPT_PACKAGE_VERSIONS,
    V8_GENERATION_AGENT_VERSION,
    V9_GENERATION_AGENT_VERSION,
    V10_GENERATION_AGENT_VERSION,
    V11_GENERATION_AGENT_VERSION,
    V12_GENERATION_AGENT_VERSION,
    V13_GENERATION_AGENT_VERSION,
    V14_GENERATION_AGENT_VERSION,
    V15_GENERATION_AGENT_VERSION,
    agent_version_for_task,
)
from casefile.agent_runtime.prompt_repository import (
    SUPPORTED_AGENT_IDS,
    PromptRepository,
    PromptRepositoryError,
    load_prompt,
    packaged_prompt_repository,
    prompt_version_for_task,
    system_prompt_for_task,
    validate_prompt_repository,
)
from casefile_contracts import TaskType

EXPECTED_CURRENT_VERSIONS = {
    "brief_polish": "brief-polish-v3",
    "brief_anchor_extract": "brief-anchor-extract-v3",
    "brief_intake_questions": "brief-intake-questions-v3",
    "brief_intake_synthesize": "brief-intake-synthesize-v2",
    "brief_strategy_options": "brief-strategy-options-v1",
    "brief_to_draft": "brief-to-draft-v15",
    "casefile_chat": "casefile-chat-v7",
    "casefile_chat_context_compactor": "casefile-chat-context-compactor-v1",
    "reverse_parse": "reverse-parse-v1",
    "idea_generation": "idea-generation-v4",
    "closure_repair": "closure-repair-v3",
    "general_mutation_planner": "general-mutation-planner-v5",
}

# This immutable release inventory starts with the authorized pre-release Chinese baseline.
EXPECTED_RELEASE_HASHES = {
    ("general_mutation_planner", "general-mutation-planner-v5"): {
        "fragment:planner": "ab4365a58559fc706b162db8998dc5c588b9caf4bc1b15060fc26ad58899c138"
    },
    ("general_mutation_planner", "general-mutation-planner-v4"): {
        "fragment:planner": "2a3dac1b29f2f98b63a6e2f557b56ad9848ec88fa237487fce3a53ccc37805cc"
    },
    ("general_mutation_planner", "general-mutation-planner-v3"): {
        "fragment:planner": "23c910ece63e6ef77b260a860d5ba007ea645d32b9a0a0b0d21539824414df7a"
    },
    ("general_mutation_planner", "general-mutation-planner-v2"): {
        "fragment:planner": "eaa09aea2fc0fc7620f9bf2ff37f20c1a42f644620bbc8fe302028a57fad7c42"
    },
    ("general_mutation_planner", "general-mutation-planner-v1"): {
        "fragment:planner": "4b6fdd45886d63dca15e7401d85edbd033e760bd1a9b9d4a0c6440a78d7e554b"
    },
    ("closure_repair", "closure-repair-v1"): {
        "fragment:repair": "e27f2e5f4d105d9718816c5c38abbb6405b1f9475e6a0f22f09a69189d58b47d"
    },
    ("closure_repair", "closure-repair-v2"): {
        "fragment:repair": "533a445ab54c53d31d864501799dadcd77bd35307a1d2571270c9b016c8d1b2f"
    },
    ("closure_repair", "closure-repair-v3"): {
        "fragment:repair": "dcd08b04e2e5ec68034d7dcfef243dcf38e84a9c66da19ec8dd7f5add66c6b2f"
    },
    ("brief_polish", "brief-polish-v2"): {
        "system": "da881f138cd88adb495f92a2b55bcd348039c8983e142eba8f023419dccd8721"
    },
    ("brief_polish", "brief-polish-v3"): {
        "system": "554f15807e88de2096aca4c6ec06d88fb516a5c285fdd5bde4425fb40712629a"
    },
    ("brief_anchor_extract", "brief-anchor-extract-v2"): {
        "system": "0c343b59def3c106698e5320c29916bc7f0d32f3514c2320a3500c21450dce6d"
    },
    ("brief_anchor_extract", "brief-anchor-extract-v3"): {
        "system": "cbbd4f6da817be6137ec6ae0a782349fe76ad987bf85e0fbf560311d19a10442"
    },
    ("brief_intake_questions", "brief-intake-questions-v1"): {
        "system": "d1f96b6bfee51b90f4c8de9cad9b8b512e6e0540a8cc2d890060255dc1337a62"
    },
    ("brief_intake_questions", "brief-intake-questions-v2"): {
        "system": "59a4cab9b080cffbf1bfc6264d07a2121dcb786f8c153f172ebe54f540656305"
    },
    ("brief_intake_questions", "brief-intake-questions-v3"): {
        "system": "357cb699b2258d929be6e152582965985e0d0937317e94255a07d6faa9c8d066"
    },
    ("brief_intake_synthesize", "brief-intake-synthesize-v1"): {
        "system": "c8ed044d334fc937698f5784e68ddd9f1decf2ff561e157560f3fcb4dca1e72c"
    },
    ("brief_intake_synthesize", "brief-intake-synthesize-v2"): {
        "system": "6d3887dde3223f7f53f78053e5ed13a36069c92b9adfa56013970c6d142017a6"
    },
    ("brief_strategy_options", "brief-strategy-options-v1"): {
        "system": "31e8fa98b451f63a41dba1c3ecd42a591a0bcc8fb6cd710a898eb74014c58f87"
    },
    ("brief_to_draft", "brief-to-draft-v3"): {
        "system": "ef8aedf9c5c72f0baeaec5eafcdcdd29238a476c99d596590ac56fe7435091ae"
    },
    ("brief_to_draft", "brief-to-draft-v4"): {
        "system": "e8a9385ee762d6c7a36ca8405e0d2e48259fbb37e9acac19fa7d5f95b69e076b"
    },
    ("brief_to_draft", "brief-to-draft-v5"): {
        "system": "62b08c5b26255965b73cccbe06ea88192fb3dc8f0eccf1265815a06e1abdb311"
    },
    ("brief_to_draft", "brief-to-draft-v6"): {
        "system": "a6b5c79908b8053a4954c9a9a6f3e00ea403c1cb07b0273944cd79d57ddb966b"
    },
    ("brief_to_draft", "brief-to-draft-v7"): {
        "system": "ffd17239f4562c86a964a3010e1ebfe1fc5c3be7c863980e74e6a0045be2a0ff"
    },
    ("brief_to_draft", "brief-to-draft-v8"): {
        "planner": "d12eee1955ed6aedf2a5b33650da88ff5c0fab97cca6a8023d2591974f4ecf73",
        "story": "223bb2bee82ca98470482eb6db5ea2737b89818770c8e50825674a83aacf42d9",
        "evidence": "3256cb833025986b80564725a1135430f35509a157246bb15bae1d4f2dfe1e0b",
        "governance": "806eb87356df8260fef0596a49729fb8c7f117bceed7a20c60b5ec391c580c6b",
    },
    ("brief_to_draft", "brief-to-draft-v9"): {
        "fragment:common": "91f8417d301c2b8a2c8cf6ae19ebe3f5e0b8aa9850bd016bd406b1b3efc10f99",
        "fragment:planner": "c89012d1b8d457ec8ef220cd12f948fbe20d7e73ff03215d38b847b9504f5045",
        "fragment:domain_common": (
            "0d20f4fe4b60668f1c19c7277d93ea29c0ee43e0939d08ee577d731c41747c82"
        ),
        "fragment:story": "b62c800d4f62b1c39fd075416b8401de1161059753450c85984efda87f0bc46e",
        "fragment:evidence": "fcb5de2bf8ee2c4068907226f16f4cf985b9bd5b4713ad6b3da8ca4823a0647a",
        "fragment:governance": "32eeecc2917449a8cb3439cd8df24e97d99764f9ddc596b171611cdc8c0d2146",
    },
    ("brief_to_draft", "brief-to-draft-v10"): {
        "fragment:common": "91f8417d301c2b8a2c8cf6ae19ebe3f5e0b8aa9850bd016bd406b1b3efc10f99",
        "fragment:planner": "945e81789befcb0e8294ccb27ac3de99097e62e294cc0bef2215bb3a5e7fbb18",
        "fragment:domain_common": (
            "0d20f4fe4b60668f1c19c7277d93ea29c0ee43e0939d08ee577d731c41747c82"
        ),
        "fragment:story": "b62c800d4f62b1c39fd075416b8401de1161059753450c85984efda87f0bc46e",
        "fragment:evidence": "db01f58b7d655e123c5a0c2f67a99c23ae1c1adcd9a156f57b273f72c832dbc9",
        "fragment:governance": "32eeecc2917449a8cb3439cd8df24e97d99764f9ddc596b171611cdc8c0d2146",
    },
    ("brief_to_draft", "brief-to-draft-v11"): {
        "fragment:common": "1471bea245e0a6f082ec34570c6e215f1ae8f39d0f669920730d4b79e2a4e0c6",
        "fragment:planner": "196f2fc74293971660670edb84cbabc1d10fb47930d8adf0c268973d9cfe15ef",
        "fragment:domain_common": (
            "30004da9ececfdb224ca51ae280d47e5e084e58252cbd418a706328e96ac55de"
        ),
        "fragment:story": "de327598d8b221a36e62728f39b6e49d4b563e7ded345142bc73cbfcd4cda128",
        "fragment:evidence": "7e1d49fbce53f1bfada49f1c1b5ab3b089d221a62fce0a0ab87fcb02ce6df646",
        "fragment:governance": "4413b0e36adf04856360c7278079185427cf71a327181234272e94de61ed1c98",
    },
    ("brief_to_draft", "brief-to-draft-v12"): {
        "fragment:common": "5a2a325867caa00779022d6a18e0cb0467ad881efd76af793ce85af065d13fca",
        "fragment:planner": "bbb57f4bd968f066467345b86ba788e5087d5b15d79561a71d0b9f08925f9ba4",
        "fragment:temporal": "434a5321dc7e114df23ec42d50fe92c4e0c4f149fa76ba7fa8d325ddc5574f6a",
        "fragment:domain_common": (
            "30004da9ececfdb224ca51ae280d47e5e084e58252cbd418a706328e96ac55de"
        ),
        "fragment:story": "ebb727a0b54af0e80cfd7473bbeedce9385790d1a856e8611c7e076363751f58",
        "fragment:evidence": "6207f57a035dd69369e91e290c904eb50541256f26a29b50e9f850b69a9e070c",
        "fragment:governance": "e8308618584c0ae881fb7a4185078493afa58cd125cdc242511bbca952cd79d5",
    },
    ("brief_to_draft", "brief-to-draft-v13"): {
        "fragment:common": "c1033e9ac83816e019d6cc8bee76010316ac178d2ba45070f86f9da09697d8d6",
        "fragment:planner": "bbb57f4bd968f066467345b86ba788e5087d5b15d79561a71d0b9f08925f9ba4",
        "fragment:temporal": "db080c9072794648f53428a6885e71b3b73c9c4fb9856e4878b7903d1d89dbd3",
        "fragment:domain_common": (
            "30004da9ececfdb224ca51ae280d47e5e084e58252cbd418a706328e96ac55de"
        ),
        "fragment:story": "ebb727a0b54af0e80cfd7473bbeedce9385790d1a856e8611c7e076363751f58",
        "fragment:evidence": "6207f57a035dd69369e91e290c904eb50541256f26a29b50e9f850b69a9e070c",
        "fragment:governance": "e8308618584c0ae881fb7a4185078493afa58cd125cdc242511bbca952cd79d5",
    },
    ("brief_to_draft", "brief-to-draft-v14"): {
        "fragment:common": "0e06a0b1643fc7a399a72d62e47fab3a6d5919c65561068cdc9c79bc0cb6ae74",
        "fragment:planner": "010d32410cbe56cce36029d611b6ae5df1b8b46a96a6f115deb37f984f617ddc",
        "fragment:temporal": "db080c9072794648f53428a6885e71b3b73c9c4fb9856e4878b7903d1d89dbd3",
        "fragment:domain_common": (
            "e5ef2e69454d7ca3c8443a3bd5c48808dbf8752010b1948d2693f8bacf0eddab"
        ),
        "fragment:story": "501b154d23f831c1060d6cb4ec4f727bd52b4f87f37488ffce15ab9a218dec04",
        "fragment:evidence": "20c3b8aca5508bf3fc2ce27c829e8c869c6e1fbc5e32b293f5cc484d0de4acd2",
        "fragment:governance": "9335ce9839adad5de5f9a49c081bf8e5ccd8d9d72305f11f874b5866413ae3dc",
    },
    ("brief_to_draft", "brief-to-draft-v15"): {
        "fragment:common": "e3b67dc37b30d6af6663ac167cb4bb08f9a913477e4b7a851a2bbadc76e47a00",
        "fragment:planner": "f54c9a161171d44fa67b988e732e082c08071c7ed5e80b5baf6b2638bf1dd6ed",
        "fragment:temporal": "d97423266dad8fb6477657c255187738049094a435170906112a51fa982ea640",
        "fragment:domain_common": (
            "e5ef2e69454d7ca3c8443a3bd5c48808dbf8752010b1948d2693f8bacf0eddab"
        ),
        "fragment:story": "66f626183ca34b7f972042fd1de8fcfac681e0f2a1f4d5a76c207052b31fac8a",
        "fragment:evidence": "0afbbfa402273e39c3c160dd7336781a4b700db71625ddbe04206e7dbe6da4c4",
        "fragment:matrix": "85ed9417d16464984c888a21d400b0bc0f45d5947a345af2411b56d6ee582e80",
        "fragment:governance": "b5934b27eb8e92261acd7f52c50a33b3fc802d86e54939de2e06d1b1d4c82c79",
    },
    ("casefile_chat", "casefile-chat-v1"): {
        "system": "7aa26994abd7ba7b7178b32e8d24140ed35fcf04c6944f41695f84e5b56020e3"
    },
    ("casefile_chat", "casefile-chat-v2"): {
        "fragment:shared": "e128cfd443879ff26c2af3ea6f732d0b86e930267bcf0578d44f95a801b89d95",  # noqa: E501
        "fragment:router": "295f587b30a39d70942bdc4c135df7cb929d46137d410508d06f7e3fcd83fee1",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "7e182c6ee59da7dcbd21a01baab00758f22cba5060bf4863ae0bfd63b2b066a8",  # noqa: E501
        "fragment:executor-analysis": "319ba419d64f08714c71f122d7bde4077ad8f68dd2089ec75efcf74079d72ef5",  # noqa: E501
        "fragment:executor-issue": "85d6e264fa23994f1d66e4228be71c0eabfe55a148392b90d42a54b56a561071",  # noqa: E501
        "fragment:executor-edit": "2b02079feef132cd4916a3922c4f0f01c0fd27beaea05cbf779db2bb46338fee",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v3"): {
        "fragment:shared": "3d69ed5d73350a7e17611f139e1cef998e0825a9ef2605a6bac0a5ab5a394d32",  # noqa: E501
        "fragment:router": "295f587b30a39d70942bdc4c135df7cb929d46137d410508d06f7e3fcd83fee1",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b40c5081234887f73f4060a3f096e21c00a35f353ad237bf051e688f63948228",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v4"): {
        "fragment:shared": "f1464bf4d05d43081bed665e505c6189e781223a9942b766076f0b4b2a086849",  # noqa: E501
        "fragment:router": "295f587b30a39d70942bdc4c135df7cb929d46137d410508d06f7e3fcd83fee1",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b40c5081234887f73f4060a3f096e21c00a35f353ad237bf051e688f63948228",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v5"): {
        "fragment:shared": "1ec39d8b124187fe8a3baae83497d90347be6d9ad63cd97985e6f71d9ef46aff",  # noqa: E501
        "fragment:router": "295f587b30a39d70942bdc4c135df7cb929d46137d410508d06f7e3fcd83fee1",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b40c5081234887f73f4060a3f096e21c00a35f353ad237bf051e688f63948228",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v6"): {
        "fragment:shared": "a55707863176685ac2f8f7c29566792bd4bd77e59a45d56f4b00b6ae00dd3d3c",  # noqa: E501
        "fragment:router": "295f587b30a39d70942bdc4c135df7cb929d46137d410508d06f7e3fcd83fee1",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b40c5081234887f73f4060a3f096e21c00a35f353ad237bf051e688f63948228",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v7"): {
        "fragment:shared": "245d08fb0b8f807ae9bdbd0c88cc6ffd6d28ce120ccd6bb63e73f40148d38671",  # noqa: E501
        "fragment:router": "295f587b30a39d70942bdc4c135df7cb929d46137d410508d06f7e3fcd83fee1",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b4befce74e6bb93526a82e71b5a8b42861f02b1d967d894865653334b421fd23",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v8"): {
        "fragment:shared": "245d08fb0b8f807ae9bdbd0c88cc6ffd6d28ce120ccd6bb63e73f40148d38671",  # noqa: E501
        "fragment:router": "1da46a3a950615ca24593ec91cf595eda2bfe045fa4a03e9d5960e30a91fdfcd",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b4befce74e6bb93526a82e71b5a8b42861f02b1d967d894865653334b421fd23",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-audit": "9662a0d627addd10659c7ed452e9fddb819f1b3b7bef7e0fb3d68947dc01d7fa",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v9"): {
        "fragment:shared": "245d08fb0b8f807ae9bdbd0c88cc6ffd6d28ce120ccd6bb63e73f40148d38671",  # noqa: E501
        "fragment:router": "1da46a3a950615ca24593ec91cf595eda2bfe045fa4a03e9d5960e30a91fdfcd",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b4befce74e6bb93526a82e71b5a8b42861f02b1d967d894865653334b421fd23",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-audit": "9871227a99400cf9729a52e85ac6b27c3fdc76d3936012aacd4db9075e31e148",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v10"): {
        "fragment:shared": "245d08fb0b8f807ae9bdbd0c88cc6ffd6d28ce120ccd6bb63e73f40148d38671",  # noqa: E501
        "fragment:router": "3ae617c6d79efa4ec9a118b96970926c5dce1ec4d3c89ddba83d9180edc5c01c",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b4befce74e6bb93526a82e71b5a8b42861f02b1d967d894865653334b421fd23",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-audit": "61e610314ccb3a73290e31a8abaf17b9b36ab7f399afbf7437a5df48e8be2da6",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v11"): {
        "fragment:shared": "245d08fb0b8f807ae9bdbd0c88cc6ffd6d28ce120ccd6bb63e73f40148d38671",  # noqa: E501
        "fragment:router": "3ae617c6d79efa4ec9a118b96970926c5dce1ec4d3c89ddba83d9180edc5c01c",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b4befce74e6bb93526a82e71b5a8b42861f02b1d967d894865653334b421fd23",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-audit": "1f3baa7c6ab91f0ea2b3e4104a234a7d7c2b01c1ca0d280652af6d61eecf07e4",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v12"): {
        "fragment:shared": "245d08fb0b8f807ae9bdbd0c88cc6ffd6d28ce120ccd6bb63e73f40148d38671",  # noqa: E501
        "fragment:router": "8878d8a8bad70bc0ba8209ff95c04c5fb18729ff39056e64f4007f0e8e0b0dd5",
        "fragment:rewrite": "d96a3b4cf1905d5aa5f0b139d591bac54d487208e63c8d178e71013ac0f69201",
        "fragment:executor-chat": "b4befce74e6bb93526a82e71b5a8b42861f02b1d967d894865653334b421fd23",  # noqa: E501
        "fragment:executor-analysis": "8d7a4b86edb02e81464739b9b72dabf1171e6e469e57659d0ba874c2d2cf5c25",  # noqa: E501
        "fragment:executor-audit": "1f3baa7c6ab91f0ea2b3e4104a234a7d7c2b01c1ca0d280652af6d61eecf07e4",  # noqa: E501
        "fragment:executor-issue": "c22f8322926e686756a5c0b755a402061bbc89a372e3b60d083c4cab3ee58d8a",  # noqa: E501
        "fragment:executor-edit": "ff2e3c728ba120967597bca81683e6ba880ad80fdd0d6c0303d510e547d36040",  # noqa: E501
        "fragment:executor-gate": "76b3ffd5aa741c6cb03c13f56f34451d688861908db9fe6de736728dcd8fe1df",  # noqa: E501
        "fragment:executor-clarify": "294ec6838eccd48fdf515116f67fc3ac04fd5e617c412ed61cb72d60e6c08d1a",  # noqa: E501
        "fragment:executor-scope": "285ca23292f6c7cca2f886f539c730dc205c9c7b0a16636d9b9634b049997ea2",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v13"): {
        "fragment:shared": "4f54caa4bf708f433084fbd66636987cfadbbc87b7623cba16083dfad55e4f93",  # noqa: E501
        "fragment:router": "c177dad4180922e118c1a2ea9648c3909593fdafec835a19032d6e9445276f49",  # noqa: E501
        "fragment:rewrite": "38c0d859578e72a889d2b03cae396c547fec436122881e068e90b89f12c5e921",  # noqa: E501
        "fragment:executor-chat": "c2c695fe5335daa3e6a3dd86bbd85d6688ddb150504751ff672b465bd3bc1070",  # noqa: E501
        "fragment:executor-analysis": "c6e7ed194b979026cf725c526e963c38d64223a3167dcc979a1f9f7d1d5d41cd",  # noqa: E501
        "fragment:executor-audit": "4481fb1965e46e482254fb8f2d75c57020757d69b3343fa80dab1d26072a8354",  # noqa: E501
        "fragment:executor-issue": "fc5e0945e57c07e0d50a672301e9aee96d71d310844c0f067d5685b0ba61a4e9",  # noqa: E501
        "fragment:executor-edit": "9e3f5b753d56f5f9b785404c0b6d8a2ec8e1baa2d1b0f40c32117815948bba85",  # noqa: E501
        "fragment:executor-gate": "8d75f248938b7004f0ac7673898898aaa253ede5a56491b4d8891b64d379dffb",  # noqa: E501
        "fragment:executor-clarify": "9a9df160de21d2a395a34bd2df3f3eca4e3c54ee8db4df7eff18952c324cc1b9",  # noqa: E501
        "fragment:executor-scope": "cb9d39fbfaf59de9bb7ba63947350905545657a454fc7560e59eb3a1a566a276",  # noqa: E501
    },
    ("casefile_chat", "casefile-chat-v14"): {
        "fragment:evidence": "aea4f79e0cf236855a8bc4c97a605eaa853a57b3f9049ec9466735a9341e280b",  # noqa: E501
        "fragment:executor-analysis": "c6e7ed194b979026cf725c526e963c38d64223a3167dcc979a1f9f7d1d5d41cd",  # noqa: E501
        "fragment:executor-audit": "45d30a13918f2f942fae0953dfe296a9b7a6e96ab5b9746b7b30cffd1606e9c2",  # noqa: E501
        "fragment:executor-chat": "c2c695fe5335daa3e6a3dd86bbd85d6688ddb150504751ff672b465bd3bc1070",  # noqa: E501
        "fragment:executor-clarify": "9a9df160de21d2a395a34bd2df3f3eca4e3c54ee8db4df7eff18952c324cc1b9",  # noqa: E501
        "fragment:executor-edit": "9e3f5b753d56f5f9b785404c0b6d8a2ec8e1baa2d1b0f40c32117815948bba85",  # noqa: E501
        "fragment:executor-gate": "8d75f248938b7004f0ac7673898898aaa253ede5a56491b4d8891b64d379dffb",  # noqa: E501
        "fragment:executor-issue": "fc5e0945e57c07e0d50a672301e9aee96d71d310844c0f067d5685b0ba61a4e9",  # noqa: E501
        "fragment:executor-scope": "cb9d39fbfaf59de9bb7ba63947350905545657a454fc7560e59eb3a1a566a276",  # noqa: E501
        "fragment:finalizer": "bb494611940b45dff868edb61b74b4b9618d593834e3e45a8083525fd0be5299",  # noqa: E501
        "fragment:rewrite": "38c0d859578e72a889d2b03cae396c547fec436122881e068e90b89f12c5e921",
        "fragment:router": "c177dad4180922e118c1a2ea9648c3909593fdafec835a19032d6e9445276f49",
    },
    ("casefile_chat", "casefile-chat-v15"): {
        "fragment:audit-common": "61ef8421fdacb6b9d65dee70365d39ccac48c400d4d4c1c1e7df65c96e69d54d",  # noqa: E501
        "fragment:audit-evidence": "e1b86d49cd462058d7a4b6314bebd951fe3c81c2ad8459f5382d26cfb6d639ef",  # noqa: E501
        "fragment:audit-finalizer": "a43d2d14722c3fb0dedc0a4eabc78770ba71d418fa10ded5ff7f8351d636881d",  # noqa: E501
        "fragment:evidence": "a3b9186a6631e691c569edaca48f59667f22f2e6267c70886611bdd7e75415bc",  # noqa: E501
        "fragment:executor-analysis": "c6e7ed194b979026cf725c526e963c38d64223a3167dcc979a1f9f7d1d5d41cd",  # noqa: E501
        "fragment:executor-chat": "c2c695fe5335daa3e6a3dd86bbd85d6688ddb150504751ff672b465bd3bc1070",  # noqa: E501
        "fragment:executor-clarify": "9a9df160de21d2a395a34bd2df3f3eca4e3c54ee8db4df7eff18952c324cc1b9",  # noqa: E501
        "fragment:executor-edit": "9e3f5b753d56f5f9b785404c0b6d8a2ec8e1baa2d1b0f40c32117815948bba85",  # noqa: E501
        "fragment:executor-gate": "8d75f248938b7004f0ac7673898898aaa253ede5a56491b4d8891b64d379dffb",  # noqa: E501
        "fragment:executor-issue": "fc5e0945e57c07e0d50a672301e9aee96d71d310844c0f067d5685b0ba61a4e9",  # noqa: E501
        "fragment:executor-scope": "cb9d39fbfaf59de9bb7ba63947350905545657a454fc7560e59eb3a1a566a276",  # noqa: E501
        "fragment:finalizer": "6ed167febbe9549dcd1ec49691ed7046a10bf469441f084b8a7cc147b5101c70",  # noqa: E501
        "fragment:rewrite": "38c0d859578e72a889d2b03cae396c547fec436122881e068e90b89f12c5e921",
        "fragment:router": "c177dad4180922e118c1a2ea9648c3909593fdafec835a19032d6e9445276f49",
    },
    ("casefile_chat_context_compactor", "casefile-chat-context-compactor-v1"): {
        "fragment:compact": "5ea1c71108018f929389f371c3a5b7ba7c451a0f696b21498f8b89cefd690ba5",  # noqa: E501
    },
    ("reverse_parse", "reverse-parse-v1"): {
        "system": "d2eaa75d1f9fabde23a0c48318abcc5542fdff1dd110bd60ffe6363878604299"
    },
    ("idea_generation", "idea-generation-v1"): {
        "system": "2cf1e5fadf31f06d8e39ef023dba579b0b1e95fba421d3fdb39ba07631a37c2a"
    },
    ("idea_generation", "idea-generation-v2"): {
        "system": "eebc2c8b8ba4b49e7a36f1cfef49d2569b9831a2da5906d7f5c91cecd3149682"
    },
    ("idea_generation", "idea-generation-v3"): {
        "system": "e594b7ecc1dd04ce8d26f425e68918d0174bdb7f4e7123e9ffe538d2352035db"
    },
    ("idea_generation", "idea-generation-v4"): {
        "system": "d0d69a92ad29a1ab6773121cf9b9f7f5ee9a7bc1b6c102d7d6b4835fa348b0b6"
    },
}


def test_packaged_registry_maps_every_agent_task_exactly_once() -> None:
    contract_task_types = {task_type.value for task_type in TaskType}
    auxiliary_agent_ids = {
        "casefile_chat_context_compactor",
        "closure_repair",
        "general_mutation_planner",
    }

    assert set(SUPPORTED_AGENT_IDS) == contract_task_types | auxiliary_agent_ids
    assert packaged_prompt_repository().expected_agent_ids == SUPPORTED_AGENT_IDS
    assert {
        agent_id: prompt_version_for_task(agent_id) for agent_id in SUPPORTED_AGENT_IDS
    } == EXPECTED_CURRENT_VERSIONS


def test_task_agent_version_identifies_component_generation_pipelines() -> None:
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v8") == V8_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v9") == V9_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v10")
        == V10_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v11")
        == V11_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v12")
        == V12_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v13")
        == V13_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v14")
        == V14_GENERATION_AGENT_VERSION
    )
    assert (
        agent_version_for_task("brief_to_draft", "brief-to-draft-v15")
        == V15_GENERATION_AGENT_VERSION
    )
    assert agent_version_for_task("brief_to_draft", "brief-to-draft-v7") == AGENT_VERSION
    assert agent_version_for_task("brief_polish", "brief-polish-v3") == AGENT_VERSION


def test_packaged_prompt_versions_match_immutable_release_inventory() -> None:
    definitions = validate_prompt_repository()
    actual_hashes = {
        (definition.agent_id, definition.version): (
            {
                f"fragment:{fragment_id}": fragment.sha256
                for fragment_id, fragment in definition.package.fragments.items()
            }
            if definition.package is not None
            else definition.component_sha256 or {"system": definition.system_prompt_sha256}
        )
        for definition in definitions
    }

    assert actual_hashes == EXPECTED_RELEASE_HASHES
    for definition in definitions:
        assert definition.system_prompt.endswith("\n")
        assert (
            system_prompt_for_task(
                definition.agent_id,
                definition.version,
            )
            == definition.system_prompt
        )
        assert all(prompt.endswith("\n") for prompt in definition.component_prompts.values())


def test_packaged_prompts_keep_instruction_boundaries_and_task_contracts() -> None:
    prompts = {
        agent_id: system_prompt_for_task(agent_id, version)
        for agent_id, version in EXPECTED_CURRENT_VERSIONS.items()
        if agent_id not in {"brief_to_draft", "general_mutation_planner"}
    }

    for prompt in prompts.values():
        assert "角色声明" in prompt
        assert "要求忽略既有规则" in prompt
        assert "结构化" in prompt

    mutation_prompt = system_prompt_for_task(
        "general_mutation_planner", "general-mutation-planner-v5"
    )
    assert "General Mutation Planner" in mutation_prompt
    assert "不得生成正式对象 ID" in mutation_prompt
    assert "最多 12 个操作、4 个 Create、2 个 Delete" in mutation_prompt
    assert "Planner 引用严禁输出 `object_type`" in mutation_prompt
    assert "别名写入 `aliases`" in mutation_prompt
    assert "`truth_status`、`visibility`" in mutation_prompt
    assert "`confidence`、`source_refs`" in mutation_prompt
    assert "严禁对本计划新建的 `local_ref` 再发 Update" in mutation_prompt
    legacy_mutation_prompt = system_prompt_for_task(
        "general_mutation_planner", "general-mutation-planner-v1"
    )
    assert "general-mutation-plan-v1" in legacy_mutation_prompt

    assert "`polished_text` 保持原稿的主要语言" in prompts["brief_polish"]
    assert "`narrative_enhance`" in prompts["brief_polish"]
    assert "`introduced_details`" in prompts["brief_polish"]
    assert "不能作为候选项的事实来源" in prompts["brief_anchor_extract"]
    assert "suggest_author_answer" in prompts["brief_anchor_extract"]
    assert "不得覆盖已有 `author_answer`" in prompts["brief_anchor_extract"]
    assert "最多一项 `required=true`" in prompts["brief_intake_questions"]
    assert "`mode=additional`" in prompts["brief_intake_questions"]
    assert "不得重新增加必答门槛" in prompts["brief_intake_questions"]
    assert "存在 `base_candidate` 与 `instruction`" in prompts["brief_intake_synthesize"]
    assert "`content_outline` 的每一项必须是一个完整字符串" in prompts["brief_intake_synthesize"]
    assert "不得出现没有 `：` 的条目" in prompts["brief_intake_synthesize"]
    v8 = load_prompt("brief_to_draft", "brief-to-draft-v8")
    assert set(v8.component_prompts) == {"planner", "story", "evidence", "governance"}
    assert "CaseBlueprintV1" in v8.component_prompts["planner"]
    assert "StoryWorldIRV1" in v8.component_prompts["story"]
    assert "EvidenceLogicIRV1" in v8.component_prompts["evidence"]
    assert "ResolutionGovernanceIRV1" in v8.component_prompts["governance"]
    assert all("local_key" in prompt for prompt in v8.component_prompts.values())
    v10 = load_prompt("brief_to_draft", "brief-to-draft-v10")
    assert "EvidenceLogicIRV2" in v10.component_prompts["evidence"]
    assert v10.package is not None
    assert v10.package.components["evidence"].output_schema_id == "evidence-logic-ir-v2"
    v11 = load_prompt("brief_to_draft", "brief-to-draft-v11")
    assert "StoryWorldIRV2" in v11.component_prompts["story"]
    assert "EvidenceLogicIRV2" in v11.component_prompts["evidence"]
    assert v11.package is not None
    assert v11.package.components["story"].output_schema_id == "story-world-ir-v2"
    assert v11.package.components["planner"].input_contract_id.endswith("input-v2")
    v12 = load_prompt("brief_to_draft", "brief-to-draft-v12")
    assert v12.package is not None
    assert set(v12.package.components) == {
        "planner",
        "temporal",
        "story",
        "evidence",
        "governance",
    }
    assert v12.package.components["temporal"].output_schema_id == "temporal-plan-v1"
    assert v12.package.components["story"].output_schema_id == "story-world-ir-v3"
    assert "不得输出 kind=unknown" in v12.component_prompts["temporal"]
    assert "严禁输出 time" in v12.component_prompts["story"]
    v13 = load_prompt("brief_to_draft", "brief-to-draft-v13")
    assert v13.package is not None
    assert v13.package.runtime_agent_version == V13_GENERATION_AGENT_VERSION
    assert "minute 禁止追加 :00" in v13.component_prompts["temporal"]
    assert "禁止追加 :00 或 :00:00" in v13.component_prompts["temporal"]
    assert "不得输出小数秒、Z、UTC 或任何时区偏移" in v13.component_prompts["temporal"]
    v14 = load_prompt("brief_to_draft", "brief-to-draft-v14")
    assert v14.package is not None
    assert v14.package.runtime_agent_version == V14_GENERATION_AGENT_VERSION
    assert "所有面向创作者的自然语言字段都必须使用简体中文" in v14.component_prompts["planner"]
    assert "不得输出纯英文标题、说明、命题、正文或判定依据" in v14.component_prompts["evidence"]
    v15 = load_prompt("brief_to_draft", "brief-to-draft-v15")
    assert v15.package is not None
    assert v15.package.runtime_agent_version == V15_GENERATION_AGENT_VERSION
    assert v15.package.components["governance"].output_schema_id == "resolution-governance-ir-v2"
    assert "它永远只是 proposed" in v15.component_prompts["governance"]
    assert "`recommended_strategy`" in prompts["brief_strategy_options"]
    assert "不得生成完整 CaseFile" in prompts["brief_strategy_options"]
    legacy_chat = load_prompt("casefile_chat", "casefile-chat-v1")
    assert "`editable_fields_by_collection`" in legacy_chat.system_prompt
    assert "未列入能力白名单" in legacy_chat.system_prompt
    assert "工作台预设指令规则" in legacy_chat.system_prompt
    assert "全卷宗体检" in legacy_chat.system_prompt
    assert "门禁结论必须逐字遵从 `validation` 快照" in legacy_chat.system_prompt
    assert "不得声称与工作台编译中心不同的结论" in legacy_chat.system_prompt
    v2_chat = load_prompt("casefile_chat", "casefile-chat-v2")
    assert v2_chat.package is not None
    assert set(v2_chat.package.components) == {
        "router",
        "rewrite",
        "chat",
        "analysis",
        "issue",
        "edit",
        "gate",
        "clarify",
        "scope",
    }
    assert v2_chat.package.runtime_agent_version == AGENT_VERSION
    assert (
        v2_chat.package.components["router"].output_schema_id
        == "casefile-chat-task-understanding-v1"
    )
    assert v2_chat.package.components["rewrite"].output_schema_id == "casefile-chat-rewrite-v1"
    assert all(
        component.output_schema_id == "casefile-chat-output-v1"
        for component_id, component in v2_chat.package.components.items()
        if component_id not in {"router", "rewrite"}
    )
    assert "危险混淆优先级" in v2_chat.component_prompts["router"]
    assert "`original_query` 永远权威" in v2_chat.component_prompts["rewrite"]
    assert "门禁结论必须逐字遵从" in v2_chat.component_prompts["gate"]
    assert "不得对不可执行动作" in v2_chat.component_prompts["scope"]
    assert v2_chat.package.components["chat"].tool_policy_id == "chat-read-v1"
    assert v2_chat.package.components["analysis"].tool_policy_id == "chat-read-v1"
    assert v2_chat.package.components["issue"].tool_policy_id == "chat-issue-v1"
    assert v2_chat.package.components["edit"].tool_policy_id == "chat-edit-v1"
    assert v2_chat.package.components["gate"].tool_policy_id == "no-tools-v1"
    assert "只能调用系统明确给出的工具" in v2_chat.component_prompts["analysis"]
    assert "`validate_patch_proposal`" in v2_chat.component_prompts["edit"]
    v3_chat = load_prompt("casefile_chat", "casefile-chat-v3")
    assert v3_chat.package is not None
    assert v3_chat.package.runtime_agent_version == AGENT_VERSION
    assert v3_chat.package.runtime_toolset_version == "casefile-chat-tools-v2"
    assert v3_chat.package.components["chat"].tool_policy_id == "chat-read-v2"
    assert v3_chat.package.components["analysis"].tool_policy_id == "chat-read-v2"
    assert v3_chat.package.components["issue"].tool_policy_id == "chat-issue-v2"
    assert v3_chat.package.components["edit"].tool_policy_id == "chat-edit-v2"
    assert "`list_casefile_records`" in v3_chat.component_prompts["chat"]
    assert "`get_related_objects`" in v3_chat.component_prompts["analysis"]
    assert "`list_casefile_records`" in v3_chat.component_prompts["issue"]
    assert "`get_related_objects`" in v3_chat.component_prompts["edit"]
    v8_chat = load_prompt("casefile_chat", "casefile-chat-v8")
    assert v8_chat.package is not None
    assert v8_chat.package.runtime_agent_version == AGENT_VERSION
    assert v8_chat.package.runtime_toolset_version == "casefile-chat-tools-v4"
    assert "audit" in v8_chat.package.components
    audit = v8_chat.package.components["audit"]
    assert audit.input_contract_id == "casefile-chat-prompt-input-v2"
    assert audit.output_schema_id == "casefile-chat-output-v1"
    assert audit.tool_policy_id == "chat-audit-v4"
    assert "logic_audit" in v8_chat.component_prompts["router"]
    assert "全卷逻辑漏洞复查" in v8_chat.component_prompts["audit"]
    assert "simulate_patch_application" in v8_chat.component_prompts["audit"]
    assert "未发现可取证漏洞" in v8_chat.component_prompts["audit"]
    v9_chat = load_prompt("casefile_chat", "casefile-chat-v9")
    assert v9_chat.package is not None
    assert v9_chat.package.previous_version == "casefile-chat-v8"
    assert v9_chat.package.runtime_toolset_version == "casefile-chat-tools-v4"
    audit_v9 = v9_chat.package.components["audit"]
    assert audit_v9.output_schema_id == "casefile-chat-output-v2"
    assert audit_v9.tool_policy_id == "chat-audit-v4"
    assert all(
        component.output_schema_id == "casefile-chat-output-v1"
        for component_id, component in v9_chat.package.components.items()
        if component_id not in {"router", "rewrite", "audit"}
    )
    assert "`audit_findings`" in v9_chat.component_prompts["audit"]
    assert "`finding_ref`" in v9_chat.component_prompts["audit"]
    assert "needs_manual_review" in v9_chat.component_prompts["audit"]
    assert "casefile-chat-output-v2" in v9_chat.component_prompts["audit"]
    v10_chat = load_prompt("casefile_chat", "casefile-chat-v10")
    assert v10_chat.package is not None
    assert v10_chat.package.previous_version == "casefile-chat-v9"
    assert v10_chat.package.runtime_toolset_version == "casefile-chat-tools-v4"
    audit_v10 = v10_chat.package.components["audit"]
    assert audit_v10.output_schema_id == "casefile-chat-output-v2"
    assert audit_v10.tool_policy_id == "chat-audit-v4"
    router_v10 = v10_chat.component_prompts["router"]
    assert "直接修改 Draft 数据" in router_v10
    assert "system_layer_direct_write" in router_v10
    assert "输出前证据链自检" in v10_chat.component_prompts["audit"]
    assert "可修漏洞而 `suggestions` 为空" in v10_chat.component_prompts["audit"]
    v11_chat = load_prompt("casefile_chat", "casefile-chat-v11")
    assert v11_chat.package is not None
    assert v11_chat.package.previous_version == "casefile-chat-v10"
    assert v11_chat.package.runtime_toolset_version == "casefile-chat-tools-v4"
    audit_v11 = v11_chat.package.components["audit"]
    assert audit_v11.output_schema_id == "casefile-chat-output-v2"
    assert audit_v11.tool_policy_id == "chat-audit-v4"
    audit_v11_prompt = v11_chat.component_prompts["audit"]
    assert "不代表本轮没有取得证据" in audit_v11_prompt
    assert "`successful_calls`" in audit_v11_prompt
    assert "不得写入盲区" in audit_v11_prompt
    assert "禁止把末尾出现的 `tool_budget_exhausted`" in audit_v11_prompt
    v12_chat = load_prompt("casefile_chat", "casefile-chat-v12")
    assert v12_chat.package is not None
    assert v12_chat.package.previous_version == "casefile-chat-v11"
    assert v12_chat.package.runtime_toolset_version == "casefile-chat-tools-v4"
    router_v12 = v12_chat.component_prompts["router"]
    assert "`sub_intents` 取值表" in router_v12
    assert "`healthcheck`" in router_v12
    assert "`evidence_chain`" in router_v12
    assert "`compare_candidates`" in router_v12
    assert "必须填对应的 `sub_intents`" in router_v12
    assert "随便查查全案逻辑漏洞，能修的就改一下" in router_v12


def test_repository_loads_an_explicit_inactive_historical_version(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(
        tmp_path,
        versions={
            "brief-polish-v1": "Role: historical prompt.\n",
            "brief-polish-v2": "Role: current prompt.\n",
        },
    )
    _write_manifest(
        root,
        version="brief-polish-v2",
        system_prompt="Role: current prompt.\n",
        previous_version="brief-polish-v1",
    )

    definitions = repository.validate()

    assert [definition.version for definition in definitions] == [
        "brief-polish-v1",
        "brief-polish-v2",
    ]
    assert (
        repository.load(
            "brief_polish",
            "brief-polish-v1",
        ).system_prompt
        == "Role: historical prompt.\n"
    )
    assert repository.load("brief_polish").version == "brief-polish-v2"


def test_repository_rejects_unknown_agent_and_version(tmp_path: Path) -> None:
    repository, _root = _one_agent_repository(tmp_path)

    with pytest.raises(PromptRepositoryError, match="Unsupported Agent Prompt"):
        repository.load("unknown_agent")
    with pytest.raises(PromptRepositoryError, match="does not belong"):
        repository.load("brief_polish", "casefile-chat-v1")
    with pytest.raises(PromptRepositoryError, match="Unknown Prompt version"):
        repository.load("brief_polish", "brief-polish-v9")


def test_repository_rejects_a_missing_system_prompt(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "brief_polish" / "v2" / "system.md").unlink()

    with pytest.raises(PromptRepositoryError, match="System Prompt .* is missing"):
        repository.load("brief_polish")


def test_repository_rejects_an_empty_system_prompt(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "brief_polish" / "v2" / "system.md").write_text("", encoding="utf-8")

    with pytest.raises(PromptRepositoryError, match="must not be empty"):
        repository.load("brief_polish")


def test_repository_rejects_system_prompt_hash_drift(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "brief_polish" / "v2" / "system.md").write_text(
        "Role: silently modified prompt.\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(PromptRepositoryError, match="System Prompt hash mismatch"):
        repository.load("brief_polish")


def test_repository_rejects_non_utf8_and_crlf_prompts(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    system_path = root / "brief_polish" / "v2" / "system.md"
    system_path.write_bytes(b"\xff\xfe")
    with pytest.raises(PromptRepositoryError, match="must be UTF-8"):
        repository.load("brief_polish")

    system_path.write_bytes(b"Role: CRLF prompt.\r\n")
    with pytest.raises(PromptRepositoryError, match="must use LF"):
        repository.load("brief_polish")


def test_repository_rejects_manifest_and_registry_drift(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    manifest_path = root / "brief_polish" / "v2" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["agent_id"] = "casefile_chat"
    _write_json(manifest_path, manifest)
    with pytest.raises(PromptRepositoryError, match="agent_id does not match"):
        repository.load("brief_polish")

    repository, root = _one_agent_repository(tmp_path / "second")
    registry_path = root / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["agents"]["brief_polish"]["current_version"] = "brief-polish-v9"
    _write_json(registry_path, registry)
    with pytest.raises(PromptRepositoryError, match="Unknown Prompt version"):
        repository.load("brief_polish")
    with pytest.raises(PromptRepositoryError, match="Current Prompt version is not present"):
        repository.validate()


def test_repository_rejects_unregistered_resources(tmp_path: Path) -> None:
    repository, root = _one_agent_repository(tmp_path)
    (root / "unexpected.txt").write_text("not part of the contract", encoding="utf-8")
    with pytest.raises(PromptRepositoryError, match="Unexpected resource.*root"):
        repository.validate()

    repository, root = _one_agent_repository(tmp_path / "second")
    (root / "brief_polish" / "v2" / "notes.md").write_text(
        "not part of this immutable version",
        encoding="utf-8",
    )
    with pytest.raises(PromptRepositoryError, match="version resources do not match"):
        repository.validate()


def _one_agent_repository(
    tmp_path: Path,
    *,
    versions: dict[str, str] | None = None,
) -> tuple[PromptRepository, Path]:
    root = tmp_path / "prompts"
    root.mkdir(parents=True)
    _write_json(
        root / "registry.json",
        {
            "schema_version": 1,
            "agents": {
                "brief_polish": {
                    "current_version": "brief-polish-v2",
                }
            },
        },
    )
    resolved_versions = versions or {"brief-polish-v2": "Role: test prompt.\n"}
    for version, system_prompt in resolved_versions.items():
        _write_manifest(root, version=version, system_prompt=system_prompt)
    return PromptRepository(root, expected_agent_ids=("brief_polish",)), root


def test_chat_audit_prompt_packages_are_execution_auditable() -> None:
    assert "casefile-chat-v8" in CHAT_PROMPT_PACKAGE_VERSIONS
    assert "casefile-chat-v9" in CHAT_PROMPT_PACKAGE_VERSIONS
    assert "casefile-chat-v10" in CHAT_PROMPT_PACKAGE_VERSIONS
    assert "casefile-chat-v11" in CHAT_PROMPT_PACKAGE_VERSIONS
    assert "casefile-chat-v12" in CHAT_PROMPT_PACKAGE_VERSIONS


def _write_manifest(
    root: Path,
    *,
    version: str,
    system_prompt: str,
    previous_version: str | None = None,
) -> None:
    version_directory = version.rsplit("-", 1)[-1]
    version_root = root / "brief_polish" / version_directory
    version_root.mkdir(parents=True, exist_ok=True)
    (version_root / "system.md").write_text(system_prompt, encoding="utf-8", newline="\n")
    _write_json(
        version_root / "manifest.json",
        {
            "agent_id": "brief_polish",
            "version": version,
            "system_prompt_file": "system.md",
            "system_prompt_sha256": sha256(system_prompt.encode("utf-8")).hexdigest(),
            "previous_version": previous_version,
            "change_summary": "Test Prompt version.",
        },
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
