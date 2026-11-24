"""Synthetic event projection checks; no live Pan authorization is implied."""
import pytest
from packages.acp.projection import TurnProjection, Unconfirmed


def frame(seq, payload, *, session='s1', execution=1):
    return {'type':'worker.stream', 'sessionId':session, 'workerId':'w1', 'generation':0,
            'executionSeq':execution, 'eventEpoch':'epoch', 'eventSeq':seq, 'event':payload}


def delta(text, cumulative, item='item-1'):
    return {'type':'content.part','role':'assistant','delta':True,'item_id':item,
            'part':{'type':'text','text':text},'stream_text':cumulative}


def receipt():
    return {'type':'queue.item_delivered', 'sessionId':'s1', 'workerId':'w1', 'generation':0,
            'executionSeq':1, 'queueItemIds':['q1']}


def test_predelivery_buffer_identity_snapshot_dedup_and_terminal():
    p = TurnProjection('s1', 'q1')
    assert p.offer(frame(1, delta('he', 'he'))) == []
    assert p.offer(frame(2, delta('secret', 'secret'), session='other')) == []
    assert p.offer(frame(3, delta('wrong', 'wrong'), execution=2)) == []
    assert p.offer(receipt()) == ['he']
    assert p.offer(frame(4, delta('llo', 'hello'))) == ['llo']
    assert p.offer(frame(4, delta('llo', 'hello'))) == []
    snapshot = {'type':'assistant','item_id':'item-1', 'final':True,
                'message':{'content':[{'type':'text','text':'hello!'}]}}
    assert p.offer(frame(5, snapshot)) == ['!']
    terminal = {'type':'worker.result','sessionId':'s1','workerId':'w1','generation':0,
                'executionSeq':1,'terminalKey':'term','resultCursor':1,'status':'done',
                'eventEpoch':'epoch','eventSeq':6,'result':'sensitive not relayed'}
    assert p.offer(terminal) == []
    assert p.terminal == {'terminalKey':'term','resultCursor':1,'status':'done'}
    assert p.offer(frame(7, delta('late', 'hellolate'))) == []


def test_ambiguous_or_lost_facts_remain_unconfirmed():
    p = TurnProjection('s1', 'q1', max_events=1)
    p.offer(frame(1, delta('a', 'a')))
    with pytest.raises(Unconfirmed, match='buffer'): p.offer(frame(2, delta('b','ab')))
    p = TurnProjection('s1', 'q1')
    with pytest.raises(Unconfirmed, match='other queue'): p.offer({**receipt(), 'queueItemIds':['q1','q2']})
    p.offer(receipt())
    with pytest.raises(Unconfirmed, match='gap'): p.offer(frame(2, delta('b', 'ab')))
    p = TurnProjection('s1', 'q1')
    p.offer(receipt()); p.offer(frame(5, delta('a','a')))
    with pytest.raises(Unconfirmed, match='order'): p.offer(frame(4, delta('b','ab')))
    with pytest.raises(Unconfirmed, match='epoch'): p.offer({**frame(6, delta('b','ab')), 'eventEpoch':'other'})


def test_snapshot_is_not_repeated_after_deltas_and_gaps_are_rejected():
    p = TurnProjection('s1', 'q1'); p.offer(receipt())
    assert p.offer(frame(1, delta('a', 'a'))) == ['a']
    assert p.offer(frame(2, delta('b', 'ab'))) == ['b']
    snapshot = {'type':'assistant', 'item_id':'item-1', 'message':{'content':[{'type':'text','text':'abc'}]}}
    assert p.offer(frame(3, snapshot)) == ['c']
    with pytest.raises(Unconfirmed, match='changed'): p.offer(frame(4, {**snapshot,
        'message':{'content':[{'type':'text','text':'different'}]}}))
    with pytest.raises(Unconfirmed, match='identity'): p.offer(frame(5,
        {'type':'assistant','message':{'content':[{'type':'text','text':'other'}]}}))
