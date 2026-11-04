"""Frozen inputs retain original dependencies and constrain proposed identities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

import pytest

from tianshu.companion.growth_materials import material, reflectable
from tianshu.companion.growth_observations import episode_material
from tianshu.companion.reflection_output import (CORRECTABLE_ERRORS, MATERIAL_PREFIX,
    frozen_episodes, prepare_reflections)
from tianshu.conversation.codec import signature
from tianshu.memory.types import Episode
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def episode(identifier, event, source, sequence, *, actual=None, origin=OriginKind.HUMAN):
    return Episode(identifier, event, 1, 'actor', origin, datetime.now(UTC), '完整经历，包含否定条件',
        (source,), ('scope',), actual, Coverage(True), sequence, f'e:{sequence}',
        person_id='person', speaker_id='person', conversation_id='chat')


def request_and_result(rule_ref='r1'):
    context = TrustedContext('principal', 'actor', 'person', 'chat', ('scope',), ('scope',), 1, OriginKind.HUMAN)
    first, second = episode('episode-1', 'event-1', SourceRef('one', 1), 1), episode('episode-2', 'event-2', SourceRef('two', 1), 2)
    rule = {'id': 'stable-rule', 'semantic_revision': 4, 'kind': 'style', 'title': '称呼',
        'content': '不使用旧称呼', 'conditions': {'when': ['称呼'], 'unless': ['明确请求']},
        'counterexamples': ['本轮明确允许时'], 'state': 'active', 'preference_kind': 'explicit',
        'conversation_id': 'chat', 'purposes': ['conversation'], 'valid_until': None,
        'source_refs': [wire(SourceRef('prior', 1))]}
    payload = material((first, second), (rule,))
    request = ModelRequest('intent', 'attempt', 'reflect', ServiceClass.ELASTIC, context,
        RequestBudget(datetime.now(UTC)+timedelta(minutes=3)),
        (ModelMessage('user', (ModelPart('text', text=MATERIAL_PREFIX+json.dumps(wire(payload), ensure_ascii=False)),)),),
        source_refs=(SourceRef('one', 1), SourceRef('two', 1), SourceRef('prior', 1)))
    note = {'kind': 'style', 'title': '称呼', 'content': '不使用旧称呼', 'episode_ids': ['e1'],
        'conditions': {'when': ['称呼'], 'unless': ['明确请求']}, 'counterexamples': ['本轮明确允许时'],
        'affect': None, 'action': 'support', 'rule_ref': rule_ref, 'preference_kind': 'explicit',
        'validity': 'persistent', 'purpose': 'conversation'}
    receipt = ModelReceipt('attempt', 'intent', 'succeeded', 'submitted', 'route', 1,
                           'provider', 'model', 'openai.chat', 'quota')
    result = ModelResult((ModelPart('text', text=json.dumps({'notes': [note]}, ensure_ascii=False)),), receipt)
    return context, request, result


def test_model_references_server_supplied_rule_and_preserves_full_generation_closure():
    context, request, result = request_and_result()
    episodes = frozen_episodes(request)
    prepared = prepare_reflections(result, context, episodes, request.source_refs, request=request)
    assert len(prepared) == 1
    item = prepared[0]
    assert item.target == {'rule_id': 'stable-rule', 'semantic_revision': 4}
    assert item.support_ids == ('episode-1',)
    assert set(item.proposal.episode_ids) == {'episode-1', 'episode-2'}
    assert {signature(ref) for ref in item.proposal.source_refs} == {signature(ref) for ref in request.source_refs}
    assert item.proposal.conditions['unless'] == ['明确请求']
    assert item.proposal.counterexamples == ('本轮明确允许时',)


def test_unsupplied_rule_or_malformed_json_is_correctable_once():
    context, request, result = request_and_result('r999')
    with pytest.raises(AppError) as rejected:
        prepare_reflections(result, context, frozen_episodes(request), request.source_refs, request=request)
    assert rejected.value.code == 'reflection_output_invalid'
    assert rejected.value.code in CORRECTABLE_ERRORS
    result = replace(result, parts=(ModelPart('text', text='{'),))
    with pytest.raises(AppError) as malformed:
        prepare_reflections(result, context, frozen_episodes(request), request.source_refs, request=request)
    assert malformed.value.code == 'reflection_output_invalid' and malformed.value.code in CORRECTABLE_ERRORS


def test_retired_frozen_proposal_protocol_is_not_reinterpreted():
    _, request, _ = request_and_result()
    part = request.messages[0].parts[0]
    payload = json.loads(part.text[len(MATERIAL_PREFIX):])
    for old_protocol in (None, 'growth.reflection.output.v3'):
        old = {**payload, 'model_protocol': old_protocol}
        message = replace(request.messages[0], parts=(replace(part, text=MATERIAL_PREFIX+json.dumps(old)),))
        with pytest.raises(AppError, match='reflection_material_contract_invalid'):
            frozen_episodes(replace(request, messages=(message,)))


def test_only_explicitly_low_information_rest_is_skipped_and_imagined_details_remain_unassessed():
    ref = SourceRef('rest', 1)
    low = episode('rest', 'rest-event', ref, 1, origin=OriginKind.ACTOR,
        actual={'derived_kind': 'routine_rest', 'learning_value': 'low', 'experience_eligible': False})
    assert not reflectable(low)
    assert reflectable(replace(low, actual_result={'derived_kind': 'routine_rest', 'experience_eligible': True}))
    imagined = replace(low, actual_result={'imagined': True, 'learning_value': 'low',
                                           'experience_eligible': False, 'title': '散步'})
    view = episode_material(imagined)
    assert view['actual_result']['imagined'] is True
    assert 'imagined_details_not_assessed' in view['reflection_coverage']['missing']
    assert material((imagined,))['low_imagined_groups'][0]['episode_ids'] == ['e1']
