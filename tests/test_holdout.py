import gzip, io, json, sqlite3, tempfile, unittest
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from backend import engine, holdout, lab, registry, runcard
from backend.config import Config


class HoldoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root/'lab.sqlite3'
        self.c = Config(start='2025-01-01', end='2025-02-01', funding_mode='off')
        self.sha = registry.engine_fingerprint(); self.key = self.c.strategy
        # Coverage is checked before an attempt is consumed; these fixtures have no cached candles.
        self.load = self.enterContext(patch.object(holdout, 'load_pair', return_value=None))
        self.save('dev')

    def save(self, rid, key=None, sha=None, **settings):
        c = Config.model_validate({**self.c.model_dump(), **settings, 'strategy': key or self.key})
        m = engine.metrics([], [{'timestamp': 0, 'equity': 1000}, {'timestamp': 1, 'equity': 1100}], 1000, 0)
        m.update(trade_count=120, expectancy_r=.2)
        self.result = {'id': rid, 'kind': 'walkforward', 'engine_sha256': sha or self.sha,
                       'config': c.model_dump(mode='json'), 'metrics': m,
                       'folds': [{'fold': 1, 'selected': {'stop_atr': 2}, 'training_ranking': [
                           {'parameters': {'stop_atr': 2}, 'eligible': True, 'score': .2, 'metrics': m}]}]}
        with registry.session(self.path) as db: registry.record_run(db, self.result)
        self.write_result()

    def write_result(self):
        with gzip.open(self.root/f'{self.result["id"]}.json.gz', 'wt') as f: json.dump(self.result, f)

    def lock(self, key=None, start='2025-03-01', end='2025-04-01'):
        return holdout.lock(key or self.key, start, end, self.path, self.root)['id']

    def ready(self, db, key):
        return {'evidence_run': registry.evidence(db, key)['id'], 'gates': [
            {'id': 'fixture_development_gate', 'status': 'pass'}, {'id': 'final_holdout', 'status': 'pending'}]}

    def evaluate(self, wid, metrics=None, error=None):
        m = metrics or {'trade_count': 100, 'net_pnl': 10, 'expectancy_r': .1, 'liquidations': 0}
        with patch.object(registry, 'gates', side_effect=self.ready), patch.object(engine, 'run_backtest', return_value={'metrics': m}, side_effect=error) as run:
            result = holdout.evaluate(wid, self.path)
        return result, run

    def test_version_identity_and_trial_count(self):
        self.save('same')
        self.save('changed', sha='a'*64)
        with registry.session(self.path) as db:
            self.assertEqual(registry.count_trials(db, [self.key]), 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM strategy_versions').fetchone()[0], 2)
        self.assertNotEqual(registry.strategy_version(self.key, self.sha), registry.strategy_version('child', self.sha))

    def test_cli_status_and_version_history_are_exposed(self):
        with patch.object(lab, 'WORKSPACE', self.root), redirect_stdout(io.StringIO()) as out:
            lab.main(['holdout', 'show', self.key])
            self.assertEqual(json.loads(out.getvalue()), {'status': 'not_configured', 'uses': 0, 'windows': []})
        with patch.object(lab, 'WORKSPACE', self.root), redirect_stdout(io.StringIO()) as out:
            lab.main(['strategy', 'show', self.key, '--json'])
            self.assertEqual(json.loads(out.getvalue())['versions'][0]['id'], registry.strategy_version(self.key, self.sha))

    def test_cli_lock_requires_explicit_dates(self):
        with self.assertRaisesRegex(SystemExit, 'requires --start and --end'):
            lab.main(['holdout', 'lock', self.key])

    def test_legacy_migration_is_idempotent_and_keeps_history(self):
        path = self.root/'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE studies (id TEXT PRIMARY KEY,strategy TEXT,kind TEXT,created TEXT,engine_sha256 TEXT,held_out INTEGER,config TEXT,metrics TEXT);'
                             'CREATE TABLE trials (study TEXT,fingerprint TEXT,strategy TEXT,role TEXT,parameters TEXT,metrics TEXT,created TEXT,PRIMARY KEY(study,fingerprint));')
            db.execute('INSERT INTO studies VALUES (?,?,?,?,?,?,?,?)', ('old', self.key, 'backtest', '2024', None, 0, '{}', '{}'))
            db.execute('INSERT INTO trials VALUES (?,?,?,?,?,?,?)', ('old', 'fingerprint', self.key, 'single', '{}', '{}', '2024'))
        for _ in range(2):
            with registry.session(path) as db:
                row = db.execute('SELECT * FROM studies').fetchone()
                self.assertEqual(row['strategy_version'], registry.strategy_version(self.key, None))
                self.assertIsNone(row['engine_sha256'])
                self.assertEqual(registry.count_trials(db, [self.key]), 1)

    def test_lock_freezes_training_winner_and_original_balance(self):
        wid = self.lock()
        with registry.session(self.path) as db:
            row = db.execute('SELECT * FROM holdout_windows WHERE id=?', (wid,)).fetchone()
            c = json.loads(row['config'])
            self.assertEqual(c['stop_atr'], 2)
            self.assertEqual(c['balance'], 1000)
            self.assertEqual(c['start'], '2025-03-01')
            self.assertEqual(row['evidence_run'], 'dev')

    def test_no_trade_final_fold_cannot_lock(self):
        self.result['folds'][0]['selected'] = None; self.write_result()
        with self.assertRaisesRegex(ValueError, 'training winner'): self.lock()

    def test_cannot_lock_research_dates_or_replace_window(self):
        with self.assertRaises(ValueError): self.lock(start='2025-01-15')
        self.lock()
        with self.assertRaisesRegex(ValueError, 'already has'): self.lock(start='2025-05-01', end='2025-06-01')

    def test_access_log_survives_failed_job_and_blocks_reservation(self):
        holdout.research_access(Config(start='2025-03-15', end='2025-04-01'), self.path)
        with self.assertRaisesRegex(ValueError, 'accessed'): self.lock()

    def test_warmup_cannot_read_locked_window(self):
        self.lock()
        with self.assertRaisesRegex(ValueError, 'warmup'):
            holdout.research_access(Config(start='2025-04-02', end='2025-05-01'), self.path)
        holdout.research_access(self.c, self.path)

    def test_pending_gates_do_not_consume_data_or_attempt(self):
        wid = self.lock()
        with patch.object(engine, 'run_backtest') as run:
            with self.assertRaisesRegex(ValueError, 'overfitting_checks'): holdout.evaluate(wid, self.path)
            run.assert_not_called()
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, self.key), {'status': 'locked', 'uses': 0})

    def test_stale_code_or_new_evidence_blocks_evaluation(self):
        wid = self.lock()
        with patch.object(registry, 'engine_fingerprint', return_value='b'*64):
            with self.assertRaisesRegex(ValueError, 'Code changed'): holdout.claim(wid, self.path)
        self.save('new')
        with self.assertRaisesRegex(ValueError, 'Evidence changed'): holdout.claim(wid, self.path)

    def test_pass_is_only_boolean_output_and_never_a_saved_run(self):
        wid = self.lock(); result, run = self.evaluate(wid)
        self.assertEqual(result, {'status': 'passed'}); self.assertEqual(run.call_count, 1)
        self.assertEqual(list(self.root.glob('*.json.gz')), [self.root/'dev.json.gz'])
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, self.key), {'status': 'passed', 'uses': 1})
            study = db.execute("SELECT * FROM studies WHERE kind='holdout'").fetchone()
            self.assertEqual(json.loads(study['metrics']), {})
            self.assertEqual(json.loads(db.execute("SELECT metrics FROM trials WHERE role='holdout'").fetchone()[0]), {})
            self.assertEqual(registry.count_trials(db, [self.key]), 2)
            self.assertEqual(next(g for g in registry.gates(db, self.key)['gates'] if g['id'] == 'final_holdout')['status'], 'pass')

    def test_failed_criteria_cannot_be_retried(self):
        wid = self.lock(); result, _ = self.evaluate(wid, {'trade_count': 99, 'net_pnl': 10, 'expectancy_r': .1, 'liquidations': 0})
        self.assertEqual(result, {'status': 'failed'})
        with self.assertRaisesRegex(ValueError, 'consumed'): holdout.claim(wid, self.path)

    def test_exception_is_redacted_and_attempt_stays_consumed(self):
        wid = self.lock(); result, _ = self.evaluate(wid, error=RuntimeError('secret net_pnl=123456'))
        self.assertEqual(result, {'status': 'failed'})
        with registry.session(self.path) as db:
            self.assertNotIn('123456', json.dumps(registry.journal(db)))
            self.assertEqual(registry.holdout_status(db, self.key)['uses'], 1)

    def test_interrupted_attempt_is_permanent_failure(self):
        wid = self.lock()
        with patch.object(registry, 'gates', side_effect=self.ready): holdout.claim(wid, self.path)
        with self.assertRaisesRegex(ValueError, 'consumed'): holdout.claim(wid, self.path)
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, self.key), {'status': 'failed', 'uses': 1})

    def test_cancellation_before_and_after_claim(self):
        wid = self.lock()
        with self.assertRaisesRegex(ValueError, 'before an attempt'):
            holdout.evaluate(wid, self.path, cancel=lambda: True)
        with registry.session(self.path) as db: self.assertEqual(registry.holdout_status(db, self.key)['uses'], 0)
        cancelled = [False]
        def run(c, cancel):
            cancelled[0] = True
            self.assertTrue(cancel())
            return {'metrics': {'trade_count': 100, 'net_pnl': 10, 'expectancy_r': .1, 'liquidations': 0}}
        with patch.object(registry, 'gates', side_effect=self.ready), patch.object(engine, 'run_backtest', side_effect=run):
            self.assertEqual(holdout.evaluate(wid, self.path, cancel=lambda: cancelled[0]), {'status': 'failed'})
        with registry.session(self.path) as db: self.assertEqual(registry.holdout_status(db, self.key)['uses'], 1)

    def test_concurrent_claims_allow_one_engine_invocation(self):
        wid = self.lock()
        def attempt():
            try: return holdout.evaluate(wid, self.path)
            except ValueError: return {'status': 'blocked'}
        with patch.object(registry, 'gates', side_effect=self.ready), patch.object(engine, 'run_backtest', return_value={'metrics': {}}) as run:
            with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(sorted(r['status'] for r in results), ['blocked', 'failed'])

    def test_lineage_counts_archived_parents_and_children(self):
        wid = self.lock(); self.evaluate(wid, error=RuntimeError())
        with registry.session(self.path) as db:
            registry.archive(db, self.key, 'failed_holdout')
            registry.add_strategy(db, 'child', parent=self.key)
        self.save('child_dev', key='child')
        with self.assertRaisesRegex(ValueError, 'accessed or reserved'): self.lock(key='child')
        child = self.lock(key='child', start='2025-05-01', end='2025-06-01'); self.evaluate(child)
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, 'child')['uses'], 2)
            self.assertEqual(registry.holdout_status(db, self.key)['uses'], 2)
            registry.reopen(db, self.key)
            self.assertEqual(registry.holdout_status(db, self.key)['uses'], 2)

    def test_legacy_holdout_counts_but_cannot_pass_current_gate(self):
        with registry.session(self.path) as db:
            db.execute('INSERT INTO holdout_ledger (strategy,engine_sha256,window_start,window_end,passed,created) VALUES (?,?,?,?,?,?)',
                       (self.key, self.sha, '2025-03-01', '2025-04-01', 1, registry.now()))
            self.assertEqual(registry.holdout_status(db, self.key)['uses'], 1)
            self.assertEqual(next(g for g in registry.gates(db, self.key)['gates'] if g['id'] == 'final_holdout')['status'], 'pending')
        with self.assertRaisesRegex(ValueError, 'accessed or reserved'): self.lock()

    def test_card_redacts_holdout_metrics(self):
        self.result.pop('folds')
        card = runcard.card(self.result, {'holdout': {'status': 'passed', 'uses': 1, 'net_pnl': 987654}})
        self.assertEqual(card['holdout'], {'status': 'passed', 'uses': 1})
        self.assertNotIn('987654', json.dumps(card))

    def test_holdout_cannot_be_exported_as_a_card(self):
        with self.assertRaisesRegex(ValueError, 'cannot be exported'):
            runcard.card({**self.result, 'kind': 'holdout'})

    def test_imported_dates_can_still_become_final_holdout(self):
        # A CSV user must load the final period before locking it; importing shows and runs nothing.
        candles = 'timestamp,open,high,low,close,volume\n2025-03-01T00:00:00Z,100,101,99,100,10\n2025-03-01T00:01:00Z,100,101,99,100,10\n'
        holdout.import_guard(candles, path=self.path)
        with registry.session(self.path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM research_access').fetchone()[0], 0)
        self.lock()

    def test_import_cannot_modify_locked_dates(self):
        wid = self.lock()
        candles = 'timestamp,open,high,low,close,volume\n2025-03-01T00:00:00Z,100,101,99,100,10\n2025-03-01T00:01:00Z,100,101,99,100,10\n'
        with self.assertRaisesRegex(ValueError, 'locked'): holdout.import_guard(candles, path=self.path)
        holdout.release(wid, self.path); holdout.import_guard(candles, path=self.path)

    def test_failed_thresholds_include_liquidation_nonfinite_and_negative_expectancy(self):
        base = {'trade_count': 100, 'net_pnl': 10, 'expectancy_r': .1, 'liquidations': 0}
        for change in [{'liquidations': 1}, {'net_pnl': 0}, {'expectancy_r': -.1}, {'net_pnl': float('nan')}]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as folder:
                row = {'strategy': self.key, 'engine_sha256': self.sha}
                path = Path(folder)/'lab.sqlite3'
                with patch.object(holdout, 'preflight'), patch.object(holdout, 'claim', return_value=(row, self.c)), patch.object(engine, 'run_backtest', return_value={'metrics': {**base, **change}}):
                    self.assertEqual(holdout.evaluate('fixture', path), {'status': 'failed'})

    def test_new_evidence_cannot_reuse_an_old_pass_for_promotion(self):
        wid = self.lock(); self.evaluate(wid); self.save('later')
        with registry.session(self.path) as db:
            self.assertEqual(next(g for g in registry.gates(db, self.key)['gates'] if g['id'] == 'final_holdout')['status'], 'pending')

    def test_lock_and_research_race_cannot_both_succeed(self):
        def lock():
            try: self.lock(); return True
            except ValueError: return False
        def access():
            try: holdout.research_access(Config(start='2025-03-01', end='2025-04-01'), self.path); return True
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            locked = pool.submit(lock); accessed = pool.submit(access)
            self.assertEqual(sum([locked.result(), accessed.result()]), 1)

    def test_exact_engine_synthetic_holdout_is_used_without_export(self):
        wid = self.lock()
        f = pd.DataFrame({'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 10000.},
                         index=pd.date_range('2025-02-01', '2025-04-01', freq='min', inclusive='left', tz='UTC'))
        bundle = (f, {}, {}, ['Synthetic only'], {}, f)
        completed = []; original = engine.run_backtest
        def exact(c, **kw):
            result = original(c, **kw); completed.append(result); return result
        with patch.object(registry, 'gates', side_effect=self.ready), patch.object(engine, 'load_pair', return_value=bundle), patch.object(engine, 'run_backtest', side_effect=exact):
            self.assertEqual(holdout.evaluate(wid, self.path), {'status': 'failed'})
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]['metrics']['trade_count'], 0)
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, self.key)['uses'], 1)


    def test_missing_data_refuses_without_consuming_the_attempt(self):
        wid = self.lock()
        self.load.side_effect = ValueError('JTOUSDT: 40,320 missing minutes in requested range.')
        with patch.object(registry, 'gates', side_effect=self.ready), patch.object(engine, 'run_backtest') as run:
            with self.assertRaisesRegex(ValueError, 'not ready.*40,320 missing minutes.*Nothing was consumed'): holdout.evaluate(wid, self.path)
            run.assert_not_called()
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, self.key), {'status': 'locked', 'uses': 0})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM studies WHERE kind='holdout'").fetchone()[0], 0)
        # Once the data is there, the same lock evaluates normally.
        self.load.side_effect = None
        self.assertEqual(self.evaluate(wid)[0], {'status': 'passed'})

    def test_gates_are_checked_before_any_data_is_loaded(self):
        wid = self.lock()
        with self.assertRaisesRegex(ValueError, 'overfitting_checks'): holdout.evaluate(wid, self.path)
        self.load.assert_not_called()

    def test_release_returns_unread_dates_and_is_journaled(self):
        wid = self.lock()
        with self.assertRaisesRegex(ValueError, 'warmup'): holdout.research_access(Config(start='2025-03-05', end='2025-03-20'), self.path)
        self.assertEqual(holdout.release(wid, self.path)['status'], 'released')
        with self.assertRaisesRegex(ValueError, 'already released'): holdout.release(wid, self.path)
        with patch.object(registry, 'gates', side_effect=self.ready):
            with self.assertRaisesRegex(ValueError, 'was released'): holdout.evaluate(wid, self.path)
        with registry.session(self.path) as db:
            self.assertEqual(registry.holdout_status(db, self.key), {'status': 'not_configured', 'uses': 0})
            self.assertEqual([w['state'] for w in registry.holdout_windows(db, self.key)], ['released'])
            self.assertIn('holdout_released', [e['kind'] for e in registry.journal(db, self.key)])
        # The same version may lock again, on the same dates: nothing was ever read.
        again = self.lock()
        self.assertEqual(self.evaluate(again)[0], {'status': 'passed'})
        with registry.session(self.path) as db: self.assertEqual([w['state'] for w in registry.holdout_windows(db, self.key)], ['passed', 'released'])

    def test_consumed_attempt_cannot_be_released(self):
        wid = self.lock(); self.evaluate(wid, error=RuntimeError())
        with self.assertRaisesRegex(ValueError, 'cannot be released'): holdout.release(wid, self.path)
        with self.assertRaisesRegex(ValueError, 'Unknown'): holdout.release('missing', self.path)
        with registry.session(self.path) as db: self.assertEqual(registry.holdout_windows(db, self.key)[0]['state'], 'failed')

    def test_code_change_after_locking_does_not_strand_the_dates(self):
        wid = self.lock()
        with patch.object(registry, 'engine_fingerprint', return_value='b'*64):
            with self.assertRaisesRegex(ValueError, 'Code changed.*Release this lock'): holdout.claim(wid, self.path)
            with self.assertRaisesRegex(ValueError, 'accessed or reserved'):
                self.save('dev_b', sha='b'*64); self.lock()
            holdout.release(wid, self.path)
            relocked = self.lock()
            with registry.session(self.path) as db:
                row = db.execute('SELECT * FROM holdout_windows WHERE id=?', (relocked,)).fetchone()
                self.assertEqual((row['engine_sha256'], row['window_start']), ('b'*64, '2025-03-01'))

    def test_fetch_downloads_the_window_with_warmup_and_is_not_research_access(self):
        from backend import data
        wid = self.lock()
        with patch.object(data, 'download_archive') as download:
            out = holdout.fetch(wid, self.path)
        pair, begin, end = download.call_args.args[:3]
        self.assertEqual((pair, str(end), out['status']), ('JTOUSDT', '2025-04-01', 'fetched'))
        self.assertLess(str(begin), '2025-03-01')
        with registry.session(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM research_access').fetchone()[0], 0)
            self.assertEqual(registry.holdout_status(db, self.key), {'status': 'locked', 'uses': 0})
        holdout.release(wid, self.path)
        with self.assertRaisesRegex(ValueError, 'released'): holdout.fetch(wid, self.path)

    def test_consumed_window_becomes_research_data_but_never_a_holdout_again(self):
        wid = self.lock(); march = Config(start='2025-03-05', end='2025-03-20')
        with self.assertRaisesRegex(ValueError, 'locked final holdout'): holdout.research_access(march, self.path)
        self.evaluate(wid, error=RuntimeError())
        holdout.research_access(march, self.path)   # The verdict is fixed; reading the period now changes nothing.
        with registry.session(self.path) as db: registry.add_strategy(db, 'child', parent=self.key)
        self.save('child_dev', key='child')
        with self.assertRaisesRegex(ValueError, 'accessed or reserved'): self.lock(key='child')

    def test_repeated_access_to_the_same_range_is_one_row(self):
        for _ in range(3): holdout.research_access(self.c, self.path)
        with registry.session(self.path) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM research_access').fetchone()[0], 1)

    def test_up_to_date_database_opens_without_a_write_lock(self):
        with registry.session(self.path): pass
        writer = sqlite3.connect(self.path, timeout=0); writer.execute('BEGIN IMMEDIATE')
        try:
            reader = sqlite3.connect(self.path, timeout=0); reader.close()
            db = registry.connect(self.path); db.execute('PRAGMA busy_timeout=0')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM studies').fetchone()[0], 1); db.close()
        finally: writer.rollback(); writer.close()

    def test_cli_release_and_show_list_windows(self):
        wid = self.lock()
        with patch.object(lab, 'WORKSPACE', self.root), redirect_stdout(io.StringIO()) as out:
            lab.main(['holdout', 'show', self.key]); shown = json.loads(out.getvalue())
        self.assertEqual((shown['status'], shown['windows'][0]['id'], shown['windows'][0]['state']), ('locked', wid, 'locked'))
        with patch.object(lab, 'WORKSPACE', self.root), redirect_stdout(io.StringIO()) as out:
            lab.main(['holdout', 'release', wid]); self.assertEqual(json.loads(out.getvalue())['status'], 'released')


if __name__ == '__main__': unittest.main()
