import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from backend import experiments, registry, runcard, verdict
from backend.config import Config
from backend.engine import metrics, simulate


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.c=Config(start='2025-01-01',end='2025-02-01',funding_mode='off')

    def run_search(self, rows, folds=1):
        calls=[]
        def fake(c,bundles,signal_overrides=None,**kw):
            training=c.start==self.c.start
            calls.append((c,training,signal_overrides))
            delta,score,n=rows[c.stop_atr] if training else (0 if signal_overrides=={} else -5,0,0)
            eq=[{'timestamp':pd.Timestamp(c.start).value//10**6,'equity':c.balance},
                {'timestamp':pd.Timestamp(c.end).value//10**6,'equity':c.balance+delta}]
            m=metrics([],eq,c.balance,0);m.update(trade_count=n,expectancy_r=score)
            return {'metrics':m,'equity':eq,'trades':[],'warnings':[str(c.start)]}
        with patch.object(experiments,'load_pair',return_value=None),patch.object(experiments,'simulate',side_effect=fake):
            result=experiments.experiment(self.c,'walkforward',{'stop_atr':list(rows)},folds=folds)
        return result,calls

    def test_nonpositive_net_or_expectancy_never_deployed(self):
        for row in [(-10,-.2,50),(10,0,50),(10,-.1,50),(0,.2,50),(-10,.2,50)]:
            with self.subTest(row=row):
                r,calls=self.run_search({1:row})
                self.assertIsNone(r['folds'][0]['selected'])
                self.assertEqual(calls[-1][2],{})
                self.assertEqual(r['metrics']['final_equity'],self.c.balance)

    def test_nonfinite_scores_and_net_are_ineligible(self):
        for value in [None,float('nan'),float('inf'),float('-inf')]:
            with self.subTest(value=value):
                r,_=self.run_search({1:(10,value,50),2:(20,.2,50)})
                self.assertEqual(r['folds'][0]['selected'],{'stop_atr':2})
        r,_=self.run_search({1:(float('inf'),.2,50)})
        self.assertIsNone(r['folds'][0]['selected'])

    def test_trade_minimum_still_applies(self):
        r,_=self.run_search({1:(100,2,19),2:(10,.1,20)})
        self.assertEqual(r['folds'][0]['selected'],{'stop_atr':2})

    def test_expectancy_r_beats_absolute_return(self):
        r,_=self.run_search({1:(100,.1,100),2:(20,.5,50)})
        self.assertEqual(r['folds'][0]['selected'],{'stop_atr':2})
        self.assertEqual(r['folds'][0]['training_ranking'][0]['score'],.5)

    def test_ties_are_deterministic(self):
        r,_=self.run_search({2:(20,.5,50),1:(100,.5,50)})
        self.assertEqual(r['folds'][0]['selected'],{'stop_atr':2})

    def test_training_winner_only_and_compounding(self):
        r,calls=self.run_search({1:(10,.1,50),2:(20,.2,50)},folds=2)
        self.assertTrue(all(f['selected']=={'stop_atr':2} for f in r['folds']))
        self.assertEqual(r['metrics']['final_equity'],990)
        self.assertEqual(len(calls),6)
        self.assertEqual(r['folds'][0]['test_end'],r['folds'][1]['test_start'])

    def test_all_cash_folds_keep_trials_and_card_findings(self):
        r,_=self.run_search({1:(-10,-.2,50),2:(-20,-.4,50)},folds=2)
        r.update(id='a'*16,engine_sha256=registry.engine_fingerprint())
        trials=list(registry.trials_of(r))
        self.assertEqual(len(trials),2)
        self.assertTrue(all(not t[3]['selected_in_folds'] for t in trials))
        with tempfile.TemporaryDirectory() as folder,registry.session(Path(folder)/'lab.sqlite3') as db:
            registry.record_run(db,r)
            self.assertEqual(registry.count_trials(db,[self.c.strategy]),2)
        card=runcard.card(r)
        # A cash fold still reports its best-ranked training candidate, marked as rejected.
        self.assertTrue(card['splits'][0]['best_rejected'])
        self.assertEqual((card['splits'][0]['metrics']['net_pnl'],card['splits'][0]['metrics']['expectancy_r']),(-10,-.2))
        self.assertIn('fold 1 training (best rejected)',runcard.digest(card))
        self.assertEqual(card['splits'][1]['metrics']['net_pnl'],0)
        self.assertEqual(card['splits'][1]['status'],'no_trade')
        ids=[f['id'] for f in card['findings']]
        self.assertIn('no_trade_folds',ids)
        self.assertNotIn('selection_stability',ids)
        self.assertIn('stayed in cash',runcard.digest(card))
        json.dumps(card,allow_nan=False)

    def test_all_cash_verdict_says_why_nothing_traded(self):
        r,_=self.run_search({1:(-10,-.2,50),2:(-20,-.4,50)},folds=2)
        v=runcard.card(r)['verdict']
        self.assertEqual((v['tone'],v['evidence']),('empty','Held out across 2 folds, 2 in cash'))
        self.assertIn('no candidate earned a test',v['headline'])
        self.assertNotIn('never triggered',v['headline']+v['detail'])
        # A run with no cash folds and no trades keeps the original wording.
        self.assertIn('never triggered',verdict.read(r['metrics'],out_of_sample=True,folds=2)['detail'])
        self.assertEqual(verdict.read(r['metrics'],out_of_sample=True,folds=1)['evidence'],'Held out across 1 fold')

    def test_cash_fold_finding_names_too_few_trades(self):
        # Profitable in training but under the 20-trade minimum: cash, and the card says which reason.
        r,_=self.run_search({1:(10,.2,5),2:(-20,-.4,50)})
        f=next(x for x in runcard.findings(r) if x['id']=='no_trade_folds')
        self.assertEqual((f['data']['folds'],f['data']['too_few_trades']),([1],[1]))
        self.assertIn('1 held-out fold stayed in cash',f['text']); self.assertIn('too few trades',f['text'])
        r,_=self.run_search({1:(-10,-.2,50),2:(-20,-.4,50)})
        self.assertEqual(next(x for x in runcard.findings(r) if x['id']=='no_trade_folds')['data']['too_few_trades'],[])

    def test_card_never_shows_negative_zero(self):
        self.assertEqual(str(runcard.r(-0.0)),'0.0'); self.assertEqual(runcard.r(-1.234),-1.23); self.assertIsNone(runcard.r(None))

    def test_mixed_folds_keep_only_actual_selections(self):
        r,_=self.run_search({1:(10,.1,50)},folds=2)
        r['folds'][0].update(selected=None,status='no_trade',no_trade_reason='No eligible candidate.')
        card=runcard.card(r)
        ids=[f['id'] for f in card['findings']]
        self.assertIn('no_trade_folds',ids)
        self.assertNotIn('selection_stability',ids)
        self.assertEqual(list(registry.trials_of(r))[0][3]['selected_in_folds'],[2])

    def test_every_fold_warning_survives(self):
        r,_=self.run_search({1:(-10,-.1,50)},folds=2)
        self.assertTrue(all(f['test_start'] in r['warnings'] for f in r['folds']))

    def test_cash_fold_uses_exact_engine_with_zero_costs(self):
        f=pd.DataFrame({'open':100.,'high':101.,'low':99.,'close':100.,'volume':100000.},
                       index=pd.date_range('2025-01-01',periods=31*1440,freq='min',tz='UTC'))
        bundle=(f,{}, {},['Synthetic market'],{'pair':'JTOUSDT'},f)
        def fake(c,bundles,signal_overrides=None,**kw):
            if c.start==self.c.start:
                m=metrics([],[{'timestamp':0,'equity':c.balance},{'timestamp':1,'equity':c.balance-10}],c.balance,0)
                m.update(trade_count=50,expectancy_r=-.1)
                return {'metrics':m}
            self.assertEqual(signal_overrides,{})
            return simulate(c,bundles,signal_overrides=signal_overrides)
        with patch.object(experiments,'load_pair',return_value=bundle),patch.object(experiments,'simulate',side_effect=fake):
            r=experiments.experiment(self.c,'walkforward',{'stop_atr':[1]},folds=2)
        self.assertEqual(r['metrics']['trade_count'],0)
        self.assertEqual(r['metrics']['final_equity'],self.c.balance)
        self.assertTrue(all(x['equity']==self.c.balance for x in r['equity']))
        self.assertEqual(r['metrics']['net_pnl'],r['metrics']['raw_pnl']-sum(r['metrics'][k] for k in ['fees','spread','slippage','funding','liquidation_fee']))
        self.assertIn('Synthetic market',r['warnings'])
