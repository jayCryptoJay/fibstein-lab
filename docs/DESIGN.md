# FibStein Lab design contract

Visual direction: restrained graphite trading-research workspace, purple actions and mint chart accents. The generated primary-screen concept is the reference; actual charts and controls are rendered in React, not embedded as a screenshot.

Tokens: page #0b0d12, surfaces #10151d/#0e141c, border #25303e, text #edf0f6, secondary #9badc5, accent #a78bfa, positive #65d6b4, adverse #f393a0. System sans UI, system monospace financial values; 4px panel/control corners; thin borders; no decorative background assets or remote fonts.

Inventory: header/wordmark and four tabs; settings rail; title plus Run backtest; three headline metrics; equity/drawdown panel; execution strip; trades; cost attribution; results provenance and warnings. Compare adds research controls, rankings and saved-run comparison. Data adds source selection, cache inventory and CSV import. Methodology explains the model.

Desktop uses independent scrollable settings/workspace with a fixed-height header/footer frame. Under 741px the settings become an explicit toggle and content flows vertically. Tables scroll inside their container instead of widening the page. Reduced-motion preferences are respected.

Intentional functional additions relative to the concept: bundled-data action, exclusive end-date label, additional required settings accordions, named preset field, stale-result warning, detailed costs, saved runs, experiment controls and provenance. Browser-window traffic-light dots from the concept are not part of the product. Concept version text was advanced from v0.1.0 to actual release v1.0.0. These additions satisfy the requested functionality without changing the primary hierarchy or palette.
