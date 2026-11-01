"""Extraction describes the existing Claims authority, without creating new subjects."""

import json
import re

import pytest
from jsonschema import Draft202012Validator

from tianshu.memory.claims import CLAIM_PREDICATE_PATTERN
from tianshu.memory.extraction_contract import extraction_instruction, extraction_schema


ALLOWED = {"person_alice", "person_bobby", "actor:chengxi", "group:current"}


def claim(subject="person_alice", predicate="interest"):
    return {
        "subject_id": subject,
        "predicate": predicate,
        "value": "原件支持的内容",
        "source_indexes": [0],
    }


def test_subject_enums_accept_actual_people_actor_group_but_not_weather_locations():
    schema = extraction_schema(ALLOWED)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for subject in ALLOWED:
        assert validator.is_valid({"claims": [claim(subject)], "matters": []})
        assert validator.is_valid(
            {
                "claims": [],
                "matters": [
                    {
                        "matter_id": "matter-1",
                        "subject_id": subject,
                        "title": "真实事项",
                        "state": "planned",
                        "source_indexes": [0],
                    }
                ],
            }
        )
    assert not validator.is_valid(
        {"claims": [claim("location:101120301", "current_temperature_c")], "matters": []}
    )
    assert not validator.is_valid(
        {
            "claims": [],
            "matters": [
                {
                    "matter_id": "matter-1",
                    "subject_id": "actor:another-role",
                    "title": "其他角色的事项",
                    "state": "planned",
                    "source_indexes": [0],
                }
            ],
        }
    )
    # Weather remains an original observation even when it has no person/actor claim.
    assert validator.is_valid({"claims": [], "matters": []})
    instruction = extraction_instruction(schema)
    assert "禁止为了满足enum把事实改绑给用户、群或角色" in instruction
    assert "返回claims:[]、matters:[]，原始工具观测仍保留" in instruction
    assert json.loads(instruction.rsplit("\n", 1)[1]) == schema
    # A later authority change gets its own enum, rather than mutating a shared global.
    narrowed = extraction_schema({"actor:chengxi"})
    assert narrowed["properties"]["claims"]["items"]["properties"]["subject_id"]["enum"] == [
        "actor:chengxi"
    ]
    assert (
        set(schema["properties"]["claims"]["items"]["properties"]["subject_id"]["enum"]) == ALLOWED
    )


@pytest.mark.parametrize(
    "predicate",
    [
        "interest",
        "current_condition",
        "current.temperature-c",
        "A0_",
        "x" * 128,
        "",
        "x" * 129,
        "当前天气",
        "two words",
        "interest\n",
        "interest\x00",
        "with/slash",
    ],
)
def test_extraction_predicate_matches_the_existing_claim_write_rule(predicate):
    validator = Draft202012Validator(extraction_schema(ALLOWED))
    assert validator.is_valid({"claims": [claim(predicate=predicate)], "matters": []}) == bool(
        re.fullmatch(CLAIM_PREDICATE_PATTERN, predicate)
    )
