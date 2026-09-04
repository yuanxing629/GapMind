"""专用 gap 抽取的版本化模型指令。"""

PROMPT_VERSION = "gap-schema3-v3"

TRAINING_INSTRUCTION = """阅读给定的一篇计算机科学论文的研究空白分析上下文，抽取核心科研实体、实体关系、核心技术路线和核心问题。默认输入是由同一篇论文的通用知识抽取结果、证据片段和论文元数据组成的结构化事实，不是未经筛选的全文。只有在输入明确标记为 core_markdown_legacy_v1 时，才把它当作经过压缩的旧版 Markdown；无论哪种模式都不得补充输入中没有出现的信息。只输出一个可解析的 JSON 对象，不要输出思考过程、Markdown 代码围栏、解释、前言或结语。

输出必须严格遵守精简 Schema 3.0。顶层字段必须且只能是 schema_version、paper、entities、relations、methods、problems。schema_version 固定为 "3.0"。

entities.type 只能是 RESEARCH_PROBLEM、TASK、METHOD、MODEL、DOMAIN、OTHER_SCIENTIFIC_TERM，禁止 DATASET、METRIC、RESULT。entities 最多 15 条，其中 OTHER_SCIENTIFIC_TERM 最多 5 条。

relations.relation_type 只能是 ADDRESSES、USES、APPLIED_TO、EXTENDS、HAS_LIMITATION、PART_OF、RELATED_TO，最多 15 条。ADDRESSES 和 HAS_LIMITATION 必须是 METHOD → RESEARCH_PROBLEM，同一方法—问题对不得同时存在这两类关系。

棋盘关系的类型边界必须保持清晰：METHOD 表示论文中被采用、评估或提出的技术路线/方法，是 ADDRESSES 和 HAS_LIMITATION 的唯一合法关系源；MODEL 只表示模型、骨干网络或实现组件，不能作为棋盘关系源。如果论文把某个模型名称作为完整技术方案进行研究，应在有原文依据时将该方案实体归为 METHOD；不能仅为通过校验而擅自改写实体含义。

methods 必须为 1 至 2 条。method_strategy_zh 是可跨论文复用的主要机制＋作用形式短标签，不得直接使用论文品牌方法名；mechanism_zh 简述机制。

problems 必须为 1 至 3 条。problem_label_zh 必须与对应 RESEARCH_PROBLEM 实体的 name_normalized_zh 完全一致，并直接表达不足、障碍、局限或未解决状态。problem_type 只能是 prior_work_gap 或 residual_limitation。prior_work_gap 必须由入选 METHOD 通过 ADDRESSES 指向；residual_limitation 必须由入选 METHOD 通过 HAS_LIMITATION 指向。claim 只是论文主张，不能仅凭 claim 自动改写成 RESEARCH_PROBLEM；只有输入事实明确表达不足、障碍、局限或未解决状态时才可生成问题。

输出前逐项自检：每个 problem 必须引用一个不同的 RESEARCH_PROBLEM；每个 method 必须引用一个不同的 METHOD；每个入选问题只能由其对应 problem_type 所要求的一类棋盘关系支撑；同一方法—问题对最多保留一条 ADDRESSES 或 HAS_LIMITATION 关系；任何关系引用都必须指向已声明且类型正确的实体；不要为了填满 1 至 3 个问题而编造实体或关系。

ID 格式分别为 E1、R1、M1、P1，所有引用必须指向存在且类型正确的实体。禁止输出 evidence、evidence_ids、paper_id、document_id、research_problems、source_file、year、venue、doi、arxiv_id、components、processing_notes。无法确定的内容不得编造。无论何时都必须闭合 JSON。

输出结构：
{
  "schema_version": "3.0",
  "paper": {"paper_name": "论文标题", "authors": [], "research_domain": []},
  "entities": [{"entity_id": "E1", "name_original": "原文名称", "name_normalized_zh": "中文名称", "type": "RESEARCH_PROBLEM", "description_zh": "说明"}],
  "relations": [{"relation_id": "R1", "source_entity_id": "E2", "relation_type": "ADDRESSES", "target_entity_id": "E1"}],
  "methods": [{"method_id": "M1", "corresponding_entity_id": "E2", "method_strategy_zh": "技术路线短标签", "mechanism_zh": "机制"}],
  "problems": [{"problem_id": "P1", "corresponding_entity_id": "E1", "problem_label_zh": "问题短标签", "problem_type": "prior_work_gap", "description_zh": "说明"}]
}"""


def repair_prompt(errors: list[str]) -> str:
    rendered = "\n".join(f"- {error}" for error in errors[:30])
    return f"""上一份 JSON 未通过 Schema 3.0 校验。请根据错误重新输出一份完整 JSON，不要解释，不要只输出补丁。

校验错误：
{rendered}

修复检查：
1. entities.type 只能是 RESEARCH_PROBLEM、TASK、METHOD、MODEL、DOMAIN、OTHER_SCIENTIFIC_TERM；删除 DATASET、METRIC、RESULT 以及不服务核心方法和问题的实体。
2. 每个 problem 必须引用不同且有效的 RESEARCH_PROBLEM，problem_label_zh 与实体中文名称完全一致。
3. prior_work_gap 使用 ADDRESSES，residual_limitation 使用 HAS_LIMITATION。
4. ADDRESSES/HAS_LIMITATION 只能是 METHOD → RESEARCH_PROBLEM，同一方法—问题对不能同时使用两类关系。
5. 修正引用或创建必要实体，删除悬空、重复、方向错误的关系。
6. 顶层只能有 schema_version、paper、entities、relations、methods、problems。
7. MODEL 不能作为 ADDRESSES 或 HAS_LIMITATION 的 source；只有在原文明确把模型作为完整技术方案时，才可将其重新归类为 METHOD。不要为了消除错误而凭空创建研究问题、方法或关系。
8. problems 和 methods 的 corresponding_entity_id 必须一一对应不同实体；不要让两个 problem 指向同一个 RESEARCH_PROBLEM，也不要让两个 method 指向同一个 METHOD。
9. 修复后重新检查每个 problem 的 problem_type、problem_label_zh、对应关系类型以及关系两端实体类型，然后输出完整 JSON。"""

