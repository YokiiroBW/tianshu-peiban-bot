"""The extraction task describes the same subjects and predicate syntax Claims accepts."""

from copy import deepcopy
import json

from .claims import CLAIM_PREDICATE_PATTERN


_EXTRACTION = {
    "type": "object",
    "required": ["claims", "matters"],
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "required": ["subject_id", "predicate", "value", "source_indexes"],
                "properties": {
                    "subject_id": {"type": "string"},
                    "predicate": {"type": "string"},
                    "value": {},
                    "assertion": {
                        "enum": ["self_confirmed", "reported", "quoted", "inferred", "observed"]
                    },
                    "polarity": {"type": "boolean"},
                    "conditions": {"type": "object"},
                    "valid_from": {"type": ["string", "null"]},
                    "valid_until": {"type": ["string", "null"]},
                    "source_indexes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
                "additionalProperties": False,
            },
        },
        "matters": {
            "type": "array",
            "maxItems": 16,
            "items": {
                "type": "object",
                "required": ["matter_id", "subject_id", "title", "state", "source_indexes"],
                "properties": {
                    "matter_id": {"type": "string"},
                    "subject_id": {"type": "string"},
                    "title": {"type": "string"},
                    "state": {
                        "enum": ["planned", "active", "rescheduled", "cancelled", "uncertain"]
                    },
                    "due_at": {"type": ["string", "null"]},
                    "summary": {"type": "string"},
                    "conditions": {"type": "object"},
                    "source_indexes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {"type": "integer", "minimum": 0},
                    },
                },
                "additionalProperties": False,
            },
        },
    },
}


def extraction_schema(allowed_subject_ids):
    """Use the live ClaimService.visible_subjects result, never subjects inferred from text."""
    schema = deepcopy(_EXTRACTION)
    allowed = sorted(set(allowed_subject_ids))
    for kind in ("claims", "matters"):
        schema["properties"][kind]["items"]["properties"]["subject_id"] = {
            "type": "string",
            "enum": allowed,
        }
    schema["properties"]["claims"]["items"]["properties"]["predicate"] = {
        "type": "string",
        # JSON Schema pattern searches; an absolute end guard preserves Claims.fullmatch,
        # including rejection of a final newline. Both Python and ECMA regex support this.
        "pattern": "^(?:" + CLAIM_PREDICATE_PATTERN + r")(?![\s\S])",
    }
    return schema


def extraction_instruction(schema):
    return (
        "从本批实际原件或有消费回执的派生观察抽取事实与事项，按JSON Schema返回唯一对象。"
        "claims和matters的subject_id只能逐字选择allowed_subject_ids及Schema enum内的真实ID；"
        "subjects目录只帮助识别授权人物和当前群，不能自造人物、角色、地点、商品或其他主体ID。"
        "只在原件明确支持该授权主体的事实或事项时抽取，禁止为了满足enum把事实改绑给用户、群或角色。"
        "例如天气地点的温度/天气观测不是某个人或角色的事实；没有相应人物/角色/群证据时"
        "返回claims:[]、matters:[]，原始工具观测仍保留，不必制造Claim才能保存观察。"
        "predicate只用Schema规定的1至128个ASCII字母、数字、下划线、点或连字符，"
        "例如interest或current_condition；中文描述和任意原文保留在value，不能当predicate。"
        "保留说话者/对象、本人确认/转述/引用、否定、条件、时间与改期取消。工具/网页/观看分析/角色自述不能成为用户本人确认。"
        "媒体只能描述consumption实际ranges/frame_times/modalities，保留omitted和采样限制，不能声称看过整段或听见缺失音轨。"
        "旧材料不能覆盖新当前态；无明确证据返回空数组，不推断已完成。source_indexes只选本批。\n"
        + json.dumps(schema, ensure_ascii=False)
    )
