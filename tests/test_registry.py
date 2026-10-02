import gzip,hashlib,json
import pytest
from backend.config import Config
from backend.engine import metrics
from backend import registry

ENGINE='e'*64

def cfg(**kw): return Config(start='2025-01-01',end='2025-02-01',**kw).model_dump(mode='json')

def stats(net=50.,trades=120,raw=None,liquidations=0,fees=10.):
    m=metrics([],[{'timestamp':0,'equity':1000.},{'timestamp':1,'equity':1000.+net}],1000,0)
    return {**m,'trade_count':trades,'raw_pnl':net+fees if raw is None else raw,'fees':fees,'liquidations':liquidations}

def backtest(rid,strategy='trend_pullback',created='2025-03-01T00:00:00+00:00',**kw):
    return {'id':rid,'kind':'backtest','created':created,'engine_sha256':ENGINE,'config':cfg(strategy=strategy,**kw),'metrics':stats()}

def walkforward(rid,strategy='trend_pullback',grid=({'stop_atr':1},{'stop_atr':1.5}),folds=2,created='2025-03-02T00:00:00+00:00',**m):
    ranking=[{'parameters':p,'score':10-i,'eligible':True,'metrics':stats()} for i,p in enumerate(grid)]
    return {'id':rid,'kind':'walkforward','created':created,'engine_sha256':ENGINE,'config':cfg(strategy=strategy),'metrics':stats(**m),
            'folds':[{'fold':i+1,'train_start':'2025-01-01','train_end':'2025-01-20','test_start':'2025-01-20','test_end':'2025-02-01',
                      'selected':grid[0],'training_ranking':ranking,'test_metrics':stats()} for i in range(folds)]}

@pytest.fixture
def db(tmp_path):
    with registry.session(tmp_path/'lab.sqlite3') as connection: yield connection

def test_backtest_is_one_trial_and_recording_is_idempotent(db):
    assert registry.record_run(db,backtest('a'*16))==1
    assert registry.record_run(db,backtest('a'*16))==0
    assert registry.count_trials(db,['trend_pullback'])==1
    assert registry.get(db,'trend_pullback')['status']=='draft'   # Registered automatically; no trial is dropped.

def test_rerunning_a_configuration_does_not_add_a_trial(db):
    registry.record_run(db,backtest('a'*16)); registry.record_run(db,backtest('b'*16))
    assert registry.count_trials(db,['trend_pullback'])==1
    registry.record_run(db,backtest('c'*16,stop_atr=2))
    assert registry.count_trials(db,['trend_pullback'])==2

def test_walkforward_counts_each_candidate_once_across_folds(db):
    assert registry.record_run(db,walkforward('a'*16,folds=3))==2
    assert registry.count_trials(db,['trend_pullback'],study='a'*16)==2
    # stop_atr=1.5 is the default: a plain backtest of it is the same trial, not a third one.
    registry.record_run(db,backtest('b'*16))
    assert registry.count_trials(db,['trend_pullback'])==2

def test_integer_and_float_parameters_are_the_same_trial():
    assert registry.fingerprint(cfg(),{'stop_atr':1})==registry.fingerprint(cfg(),{'stop_atr':1.0})==registry.fingerprint(cfg(stop_atr=1))
    assert registry.fingerprint(cfg(),{'stop_atr':1})!=registry.fingerprint(cfg(),{'stop_atr':2})

def test_compare_and_stress_trials(db):
    row=lambda **s:{'settings':s,'metrics':stats()}
    registry.record_run(db,{'id':'a'*16,'kind':'compare','created':'2025-03-01T00:00:00+00:00','config':cfg(),
                            'rows':[row(strategy=s) for s in ['trend_pullback','breakout_retest','vwap_reversion']]})
    assert [registry.count_trials(db,[s]) for s in ['trend_pullback','breakout_retest','vwap_reversion']]==[1,1,1]
    registry.record_run(db,{'id':'b'*16,'kind':'stress','created':'2025-03-01T00:00:00+00:00','config':cfg(),
                            'rows':[row(stress_multiplier=x) for x in [1,2,3]]})
    # Cost multipliers are assumptions about one configuration, not three candidates.
    assert registry.count_trials(db,['trend_pullback'])==1

def test_archived_trials_still_count_across_the_lineage(db):
    registry.record_run(db,backtest('a'*16))
    registry.add_strategy(db,'pullback_long',parent='trend_pullback',origin='ai',hypothesis='Longs only.')
    registry.record_run(db,backtest('b'*16,strategy='pullback_long')); registry.record_run(db,backtest('c'*16,strategy='pullback_long',stop_atr=2))
    registry.archive(db,'pullback_long','overfit','Failed held out.')
    registry.add_strategy(db,'pullback_slow',parent='trend_pullback')
    lib={r['key']:r for r in registry.library(db)}
    assert lib['pullback_long']['status']=='archived' and lib['pullback_long']['trials']==2
    assert lib['pullback_slow']['trials']==0 and lib['pullback_slow']['lineage_trials']==3
    assert registry.context(db,backtest('d'*16,strategy='pullback_slow'))['selection']['label']=='best of 3'

def test_candidate_needs_held_out_evidence(db):
    registry.record_run(db,backtest('a'*16))
    g=registry.gates(db,'trend_pullback',current_engine=ENGINE)
    assert g['gates'][0]['id']=='held_out_evidence' and not g['candidate_ready']
    with pytest.raises(ValueError,match='held_out_evidence'): registry.promote(db,'trend_pullback',current_engine=ENGINE)

def test_gates_pass_to_candidate_but_never_past_pending_checks(db):
    registry.record_run(db,walkforward('a'*16))
    g=registry.gates(db,'trend_pullback',current_engine=ENGINE)
    assert g['candidate_ready'] and not g['promotion_ready'] and g['evidence_run']=='a'*16
    assert {x['id']:x['status'] for x in g['gates']}=={'held_out_trades':'pass','profitable_after_costs':'pass','survives_doubled_costs':'pass',
        'no_liquidations':'pass','engine_unchanged':'pass','overfitting_checks':'pending','final_holdout':'pending'}
    assert registry.promote(db,'trend_pullback',current_engine=ENGINE)['status']=='candidate'
    with pytest.raises(ValueError,match='overfitting_checks'): registry.promote(db,'trend_pullback',current_engine=ENGINE)
    assert registry.get(db,'trend_pullback')['status']=='candidate'

# raw 100 with 60 of costs nets 40 today and -20 with costs doubled: profitable, but not a candidate.
@pytest.mark.parametrize('change,failing',[({'trades':99},['held_out_trades']),({'net':-1.},['profitable_after_costs','survives_doubled_costs']),
    ({'net':40.,'raw':100.,'fees':60.},['survives_doubled_costs']),({'liquidations':1},['no_liquidations'])])
def test_each_evidence_gate_blocks_promotion(db,change,failing):
    registry.record_run(db,walkforward('a'*16,**change))
    g=registry.gates(db,'trend_pullback',current_engine=ENGINE)
    assert [x['id'] for x in g['gates'] if x['status']=='fail']==failing
    with pytest.raises(ValueError,match=failing[0]): registry.promote(db,'trend_pullback',current_engine=ENGINE)
    assert registry.get(db,'trend_pullback')['status']=='draft'

def test_evidence_is_the_latest_held_out_run_not_the_best(db):
    registry.record_run(db,walkforward('a'*16,created='2025-03-01T00:00:00+00:00'))
    registry.record_run(db,walkforward('b'*16,created='2025-03-05T00:00:00+00:00',net=-30.))
    assert registry.gates(db,'trend_pullback',current_engine=ENGINE)['evidence_run']=='b'*16
    assert not registry.gates(db,'trend_pullback',current_engine=ENGINE)['candidate_ready']

def test_engine_change_invalidates_evidence(db):
    registry.record_run(db,walkforward('a'*16))
    g=registry.gates(db,'trend_pullback',current_engine='f'*64)
    assert [x['id'] for x in g['gates'] if x['status']=='fail']==['engine_unchanged']

def test_archive_needs_a_fixed_reason_and_reopen_restores_draft(db):
    registry.record_run(db,backtest('a'*16))
    with pytest.raises(ValueError,match='Archive reason'): registry.archive(db,'trend_pullback','did not like it')
    registry.archive(db,'trend_pullback','cost_fragile','Edge gone at 2x.')
    with pytest.raises(ValueError,match='archived'): registry.promote(db,'trend_pullback',current_engine=ENGINE)
    assert registry.count_trials(db,['trend_pullback'])==1
    assert registry.reopen(db,'trend_pullback')['archive_reason'] is None
    kinds=[e['kind'] for e in registry.journal(db,'trend_pullback')]
    assert kinds==['status','status','registered']

def test_strategy_registration_rules(db):
    with pytest.raises(ValueError,match='lowercase'): registry.add_strategy(db,'Bad Key')
    with pytest.raises(ValueError,match='Parent'): registry.add_strategy(db,'child',parent='missing')
    with pytest.raises(ValueError,match='Origin'): registry.add_strategy(db,'idea',origin='tarot')
    registry.add_strategy(db,'idea',origin='pine',hypothesis='x')
    with pytest.raises(ValueError,match='already registered'): registry.add_strategy(db,'idea')
    assert registry.family(db,'idea')==['idea']

def test_context_reports_parent_delta_and_comparability(db):
    registry.record_run(db,backtest('a'*16))
    registry.add_strategy(db,'child_idea',parent='trend_pullback')
    child=backtest('b'*16,strategy='child_idea'); child['metrics']=stats(net=80.)
    registry.record_run(db,child); c=registry.context(db,child,current_engine=ENGINE)
    assert c['parent']['key']=='trend_pullback' and c['parent']['run']=='a'*16 and c['parent']['comparable']
    assert c['parent']['delta']['net_return_pct']==pytest.approx(3.0)
    assert c['engine_current'] is True and c['holdout']=={'status':'not_configured','uses':0}
    other=backtest('c'*16,strategy='child_idea',pairs=['SOLUSDT'])
    assert registry.context(db,other,current_engine=ENGINE)['parent']['comparable'] is False

def test_sync_backfills_saved_runs_and_skips_unreadable_files(db,tmp_path):
    runs=tmp_path/'runs'; runs.mkdir()
    for r in [backtest('a'*16),walkforward('b'*16)]:
        with gzip.open(runs/f'{r["id"]}.json.gz','wt') as f: json.dump(r,f)
    (runs/('c'*16+'.json.gz')).write_bytes(b'not gzip')
    assert registry.sync(db,runs)=={'added':2,'unreadable':1}
    assert registry.sync(db,runs)=={'added':0,'unreadable':1}
    assert registry.count_trials(db,['trend_pullback'])==2

def test_engine_fingerprint_matches_the_digest_saved_on_runs():
    source=b''.join((registry.ROOT/'backend'/x).read_bytes() for x in ['engine.py','strategies.py','config.py','data.py','experiments.py'])
    if (registry.ROOT/'custom_strategies.py').exists(): source+=(registry.ROOT/'custom_strategies.py').read_bytes()
    assert registry.engine_fingerprint()==hashlib.sha256(source).hexdigest()

def test_saving_a_run_records_its_trials(tmp_path,monkeypatch):
    from backend import server
    monkeypatch.setattr(server,'DB',tmp_path/'lab.sqlite3'); monkeypatch.setattr(server,'RESULTS',tmp_path)
    with registry.session(server.DB) as db: db.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, created TEXT NOT NULL, kind TEXT NOT NULL, config TEXT NOT NULL, metrics TEXT NOT NULL)')
    result=walkforward('x'*16); del result['id']
    rid=server.persist_result(result,'walkforward')
    assert (tmp_path/f'{rid}.json.gz').exists() and result['engine_sha256']==registry.engine_fingerprint()
    with registry.session(server.DB) as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==1
        assert registry.count_trials(db,['trend_pullback'],study=rid)==2
    k=server.card_of(rid); assert k['selection']['label']=='best of 2' and k['run']['engine_current'] is True
    assert k['verdict']['evidence']=='Held out across 2 folds'
