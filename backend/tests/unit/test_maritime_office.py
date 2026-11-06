from decimal import Decimal
import json

import pytest

from backend.application.maritime.calculator import calculate_claim
from backend.application.maritime.service import MaritimeError, MaritimeOffice
from backend.infrastructure.search.maritime_corpus import MaritimeCorpus, build_index


@pytest.mark.asyncio
async def test_online_search_citations_resolve_without_local_index(tmp_path, monkeypatch):
    import httpx
    from backend.infrastructure.search import maritime_corpus

    class SearchClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            source = {'片段编号':'HH1:1','案例标题':'货损纠纷','文本':'集装箱货损赔偿争议','案例编号':'HH1','原文位置':'段1'}
            return httpx.Response(200, request=httpx.Request('POST',url),json={'hits':{'hits':[{'_source':source}]}})

    monkeypatch.setattr(maritime_corpus.httpx,'AsyncClient',SearchClient)
    monkeypatch.setattr(maritime_corpus,'dotenv_values',lambda *a,**k:{'OPENSEARCH_INITIAL_ADMIN_PASSWORD':'fixture-only'})
    corpus = MaritimeCorpus(tmp_path/'not-built.db')
    result = await corpus.search('货损','cases')
    assert result['engine'] == 'opensearch-bm25'
    item = result['items'][0]
    assert corpus.source('cases',item['id'])['text'] == item['text']


def test_private_skill_history_does_not_become_public_on_publish(tmp_path):
    office = MaritimeOffice(tmp_path/'office.db')
    private = office.save_skill('author', dict(name='method', summary='summary', instructions='private-client-facts'))
    public = office.save_skill('author', dict(name='method', summary='summary', instructions='clean public method', visibility='community', expected_version=1), private['id'])
    assert office.skill('reader', private['id'])['instructions'] == 'clean public method'
    assert office.skill('author', private['id'], 1)['instructions'] == 'private-client-facts'
    with pytest.raises(MaritimeError):
        office.skill('reader', public['id'], 1)


def test_private_skills_are_owner_scoped_and_conflicts_preserve_version(tmp_path):
    office = MaritimeOffice(tmp_path/'office.db')
    skill = office.save_skill('a', dict(name='a',summary='s',instructions='private'))
    with pytest.raises(MaritimeError):
        office.skill('b',skill['id'])
    with pytest.raises(MaritimeError):
        office.save_skill('a',dict(name='a',summary='s',instructions='changed',expected_version=0),skill['id'])
    assert MaritimeOffice(tmp_path/'office.db').skill('a',skill['id'])['version'] == 1
    assert len([s for s in office.skills('b') if s['visibility']=='builtin']) == 8


def test_case_membership_review_versioning_and_revocation(tmp_path):
    office = MaritimeOffice(tmp_path/'office.db')
    case = office.create_case('owner','cargo claim','Unconfirmed facts')
    key = case['id']
    with pytest.raises(MaritimeError):
        office.case('other',key)
    office.add_member('owner',key,'writer','collaborator')
    office.add_member('owner',key,'reviewer','reviewer')
    with pytest.raises(MaritimeError):
        office.add_member('writer',key,'intruder','reviewer')
    draft = office.save_entry('writer',key,dict(kind='document',title='Draft',content='v1',status='draft'))
    reviewed = {**draft,'status':'reviewed','expected_version':1}
    with pytest.raises(MaritimeError):
        office.save_entry('writer',key,reviewed,draft['id'])
    accepted = office.save_entry('reviewer',key,reviewed,draft['id'])
    assert accepted['version'] == 2
    assert accepted['status'] == 'reviewed'
    changed = office.save_entry('writer',key,{**accepted,'status':'draft','content':'v3','expected_version':2},draft['id'])
    assert changed['status'] == 'draft'
    with pytest.raises(MaritimeError):
        office.save_entry('owner',key,{**reviewed,'expected_version':1},draft['id'])
    office.remove_member('owner',key,'writer')
    with pytest.raises(MaritimeError):
        office.case('writer',key)
    with office.connection() as db:
        assert db.execute('SELECT count(*) FROM entry_versions').fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM audit WHERE action='member.removed'").fetchone()[0] == 1


def test_assignments_and_dates_cannot_be_invented(tmp_path):
    office = MaritimeOffice(tmp_path/'office.db')
    key = office.create_case('owner','Case')['id']
    for value in [dict(assignee_id='not-a-member'),dict(due_at='2026-02-30')]:
        with pytest.raises(MaritimeError):
            office.save_entry('owner',key,dict(title='task',**value))
    assert office.case('owner',key)['entries'] == []


def test_exact_claims_group_currencies_and_round_items():
    result = calculate_claim([dict(name='damage',quantity='3',rate='0.1',currency='CNY'),dict(name='days',quantity='2.5',rate='12.31',currency='USD')])
    assert result['totals'] == {'CNY':'0.30','USD':'30.78'}
    assert Decimal(result['items'][0]['amount']) == Decimal('0.30')


@pytest.mark.parametrize('number',['NaN','Infinity','-1','1e999999','0.000000001',True,None])
def test_invalid_claim_inputs_rejected(number):
    with pytest.raises(ValueError):
        calculate_claim([dict(name='x',quantity=number,rate='1',currency='CNY')])


def test_local_corpus_is_real_versioned_and_read_only(tmp_path):
    law = tmp_path/'law.jsonl'
    law.write_text('\n'.join(json.dumps(dict(chunk_id=k,document_name='海商法',chunk_text='承运人承担货物损坏责任',is_current=current,version_date='2025-01-01',article_no='第一条'),ensure_ascii=False) for k,current in [('new',True),('old',False)]),encoding='utf-8')
    case = tmp_path/'case.jsonl'
    case.write_text(json.dumps({'片段编号':'HH1:1','案例标题':'货损纠纷','文本':'集装箱货损赔偿争议','案例编号':'HH1','原文位置':'段1'},ensure_ascii=False),encoding='utf-8')
    index = tmp_path/'corpus.db'
    assert build_index(index,{'laws':law,'cases':case}) == {'laws':2,'cases':1}
    corpus = MaritimeCorpus(index)
    assert [r['id'] for r in corpus.local_search('承运人','laws')] == ['new']
    assert len(corpus.local_search('承运人','laws',False)) == 2
    assert corpus.local_search('货损','cases')[0]['case_id'] == 'HH1'
    assert corpus.source('laws','old')['current'] is False
    with pytest.raises(LookupError):
        corpus.source('laws','HH1:1')
    assert build_index(index,{'laws':law,'cases':case}) == {'laws':2,'cases':1}
