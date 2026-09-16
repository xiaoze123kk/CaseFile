生成 StoryWorldIRV3 artifact，只包含 entities、relationships、locations、events。人物必须使用具体姓名；关系必须表达真实语义；地点与事件遵守输入的空间和引用约束。事件不得输出时间字段，服务端会从 Temporal Plan 注入。

实体与地点的 name、所有对象的 description、关系与事件的 title，以及 traits、goals、secrets、capabilities、access_rules、visibility_rules、tags 等自然语言数组项都使用简体中文。entities 的 local_key 集合必须与 Blueprint 的 entities 集合完全相等；地点、建筑、房间写入 locations，不得作为 entity。

person 的 name 必须是真实姓名、化名或 Brief/Blueprint 给出的固定代号，禁止把主角、嫌疑人、凶手、受害者、目击者、侦探、警察、医生、管家、邻居、神秘人、幕后黑手、主谋等身份角色词当作 name；这些身份写进 traits 或 description。Brief 未给姓名时起自然简体中文姓名；不同人物不得共用同名。organization 等专名也应使用具体专名，不得用“公司”“组织”等泛化类型词占位。

Temporal Plan 是唯一时间权威。事件严禁输出 time、日期、时间范围、相对锚点或任何替代时间字段；服务端按 event local_key 注入 assignment。entity_type 只能是 person、organization、object、system、faction、rule_actor、other；地点优先表达有依据的 parent_key、adjacency_keys、travel_times、access_rules、visibility_rules，WGS84 只能逐值使用 allowed_wgs84_coordinates。cause_keys 只能指向时间更早事件，effect_keys 只能指向更晚事件；无法确定时留空。relationship 只表示实体与实体关系，from_key/to_key 严格使用 entities 白名单，relationship_type 使用小写机器标识。

落实 owner_branch=story_world 的目标。若 plan_context 含 nag_reminder，必须在本次 plan_checkin 中逐项回应，但不得为了标记完成而提前揭露或改变 Blueprint。
