from datetime import UTC, datetime, timedelta

import pytest

from tianshu.life.continuation import options, resume_step, resumed_checkpoint
from tianshu.life.day_plan import awake_window, next_gap
from tianshu.runtime.errors import AppError


def test_receipt_pagination_and_forum_followup_are_distinct_selections():
    page={'capability_id':'content.discover','arguments':{'query':'actual topic','cursors':{'source':'page2'}}}
    follow={'capability_id':'content.read','arguments':{'url':'https://example.test/topic','after_post_number':42}}
    row={'action_id':'read','checkpoint_data':{'completed_action_id':'read','capability_id':'content.read',
        'exploration':{'next_actions':[],'discovery_continuation':page,'current_item':{'follow_up':follow}}}}
    assert {item['key']:item['min_after_minutes'] for item in options(row['checkpoint_data'])}=={'discovery_page':0,'follow_up':30}
    assert resume_step(row,'discovery_page')==page
    assert resume_step(row,'follow_up')==follow
    assert resumed_checkpoint(row,'discovery_page')['exploration']['discovery_continuation'] is None
    row['checkpoint_data']['exploration']['next_actions']=[{'candidate_key':'unread','capability_id':'content.read','arguments':{'url':'https://example.test/unread'}}]
    with pytest.raises(AppError,match='activity_continuation_invalid'):
        resume_step(row,'discovery_page')
    row['checkpoint_data']['completed_action_id']='another-action'
    with pytest.raises(AppError,match='activity_action_unresolved'):
        resume_step(row,'follow_up')


def test_day_window_and_gap_leave_existing_commitment_space():
    now=datetime(2026,11,2,22,30,tzinfo=UTC)
    routine={'timezone':'UTC','wake_at':'08:00','sleep_at':'23:00','idle_seconds':5400}
    begin,end=awake_window(routine,now)
    assert begin==now and end==datetime(2026,11,2,23,tzinfo=UTC)
    assert next_gap(now,timedelta(minutes=20),end,[(now+timedelta(minutes=5),end)]) is None
    asleep,wake=awake_window(routine,end)
    assert asleep is None and wake==datetime(2026,11,3,8,tzinfo=UTC)
