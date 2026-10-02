import importlib.util, tempfile, unittest
from pathlib import Path
from unittest.mock import ANY, patch
from backend import holdout, registry
from backend.config import Config


@unittest.skipUnless(importlib.util.find_spec('fastapi'), 'Pinned FastAPI dependency is unavailable.')
class HoldoutApiTests(unittest.TestCase):
    def setUp(self):
        from backend import server
        self.server = server
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)/'lab.sqlite3'
        self.enterContext(patch.object(server, 'DB', self.path))
        self.enterContext(patch.object(server, 'RESULTS', Path(folder.name)))
        self.c = Config(start='2025-01-01', end='2025-02-01')

    def test_holdout_cannot_enter_public_run_store(self):
        with self.assertRaisesRegex(ValueError, 'cannot be saved'):
            self.server.persist_result({'config': self.c.model_dump(mode='json')}, 'holdout')
        self.assertFalse(list(self.path.parent.glob('*.json.gz')))

    def test_api_rejects_locked_research_before_engine(self):
        with registry.session(self.path) as db:
            db.execute('INSERT INTO holdout_windows VALUES (?,?,?,?,?,?,?,?,?)',
                       ('window', self.c.strategy, 'version', 'engine', 'evidence', '2025-01-01', '2025-02-01', '{}', registry.now()))
        with patch.object(self.server, 'launch', side_effect=lambda fn: fn(lambda _: None, lambda: False)), patch.object(self.server, 'run_backtest') as run:
            with self.assertRaisesRegex(ValueError, 'locked'):
                self.server.run(self.server.RunRequest(config=self.c))
            run.assert_not_called()

    def test_api_holdout_evaluation_uses_only_private_service(self):
        with patch.object(self.server, 'launch', side_effect=lambda fn: fn(lambda _: None, lambda: False)), patch.object(holdout, 'evaluate', return_value={'status': 'failed'}) as evaluate, patch.object(self.server, 'persist_result') as persist:
            self.assertEqual(self.server.evaluate_holdout('window'), {'status': 'failed'})
            evaluate.assert_called_once_with('window', self.path, cancel=ANY); persist.assert_not_called()

    def test_api_status_counts_lineage_without_performance(self):
        with registry.session(self.path) as db:
            registry.ensure(db, self.c.strategy); registry.add_strategy(db, 'child', parent=self.c.strategy)
            db.execute('INSERT INTO holdout_ledger (strategy,engine_sha256,window_start,window_end,passed,created) VALUES (?,?,?,?,?,?)',
                       (self.c.strategy, 'old', '2025-03-01', '2025-04-01', 0, registry.now()))
        self.assertEqual(self.server.holdout_status('child'), {'status': 'not_configured', 'uses': 1})
