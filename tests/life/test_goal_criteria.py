from dataclasses import replace

from jsonschema import Draft202012Validator

from tianshu.life.directions import criterion_schema, milestone
from tianshu.types import Coverage, Receipt


def test_sampled_reading_completes_only_the_actual_requested_window():
    criteria={'kind':'reading_complete','capability_id':'video.observe','target_count':1}
    reading={'analysis_completed':True,'requested_completed':True,'source':{'source_id':'video','revision':2},
        'requested':{'start':0,'end':30},'next_position':None,'omitted':[{'start':3,'end':5}]}
    result=Receipt('receipt','action','attempt','succeeded','none','not_requested',result={'consumption':reading})
    assert milestone(criteria,'video.observe',result,{'id':'activity'})
    assert milestone(criteria,'video.observe',replace(result,result={'consumption':{**reading,'requested_completed':False}}),{'id':'activity'}) is None
    assert milestone(criteria,'video.observe',replace(result,result={'consumption':{**reading,'analysis_completed':False}}),{'id':'activity'}) is None
    assert milestone(criteria,'video.observe',replace(result,result={**result.result,'continuation':{'capability_id':'video.observe','arguments':{}}}),{'id':'activity'}) is None


def test_queued_writing_is_never_a_saved_chapter_and_criteria_requires_work():
    validator=Draft202012Validator(criterion_schema())
    criteria={'kind':'chapter_saved','capability_id':'writing.request','target_count':2,'work_id':'work'}
    assert validator.is_valid(criteria)
    assert not validator.is_valid({key:value for key,value in criteria.items() if key!='work_id'})
    assert not validator.is_valid({**criteria,'capability_id':'content.read'})
    queued=Receipt('receipt','action','attempt','succeeded','confirmed','not_requested',result={'accepted':True})
    assert milestone(criteria,'writing.request',queued,{'id':'activity'}) is None


def test_web_and_persisted_reading_use_their_real_completion_shapes():
    criterion={'kind':'reading_complete','capability_id':'content.read','target_count':1}
    result=Receipt('receipt','action','attempt','succeeded','none','not_requested',coverage=Coverage(True),
        result={'read_basis':'actual_source_body','source':{'source_id':'page','revision':1},'content_key':'actual-page'})
    assert milestone(criterion,'content.read',result,{'id':'activity'})
    assert milestone(criterion,'content.read',replace(result,result={'read_basis':'no_new_content'}),{'id':'activity'}) is None
    reading={'id':'read','source_ref':{'source_id':'book','revision':2},'state':'completed','content':'actual source text',
        'completion_basis':'source_read_not_understanding','analysis_completed':False,'next_position':None}
    assert milestone({**criterion,'capability_id':'life.reading'},'life.reading',replace(result,result={'reading':reading}),{'id':'activity'})
    assert not Draft202012Validator(criterion_schema()).is_valid({**criterion,'capability_id':'unimplemented.reader'})
