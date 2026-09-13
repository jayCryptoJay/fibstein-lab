"""Validated, serializable settings. Percent and basis-point units are explicit."""
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

PAIRS = ['JTOUSDT','SOLUSDT','BTCUSDT','ETHUSDT','XRPUSDT','DOGEUSDT','AVAXUSDT','LINKUSDT','SUIUSDT','ADAUSDT']

class Config(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    pairs: list[str] = Field(default_factory=lambda:['JTOUSDT'], min_length=1, max_length=20)
    start: date = date(2025,9,1)
    end: date = date(2026,9,1)
    source: Literal['binance_archive','bybit','okx','binanceusdm','csv'] = 'binance_archive'
    strategy: str = 'trend_pullback'
    timeframe: Literal[5,15,30,60] = 15
    execution_minutes: Literal[1,5] = 1
    balance: float = Field(1000, gt=0, le=1e9)
    leverage: float = Field(5, ge=1, le=50)
    risk_pct: float = Field(.5, gt=0, le=10)
    sizing: Literal['risk','fixed_notional'] = 'risk'
    fixed_notional: float = Field(100, gt=0)
    max_positions: int = Field(3, ge=1, le=20)
    max_exposure_pct: float = Field(300, gt=0, le=5000)
    max_margin_pct: float = Field(60, gt=0, le=100)
    max_open_risk_pct: float = Field(2, gt=0, le=50)
    margin_mode: Literal['isolated','cross'] = 'isolated'
    direction: Literal['both','long','short'] = 'both'
    maker_bps: float = Field(2, ge=0, le=100)
    taker_bps: float = Field(5, ge=0, le=100)
    slippage_bps: float = Field(3, ge=0, le=500)
    spread_bps: float = Field(2, ge=0, le=500)
    stress_multiplier: float = Field(1, ge=1, le=10)
    funding_mode: Literal['historical','estimate','off'] = 'historical'
    funding_bps: float = Field(1, ge=-100, le=100)
    funding_interval_hours: int = Field(8, ge=1, le=24)
    maintenance_margin_pct: float = Field(.5, gt=0, le=10)
    liquidation_fee_bps: float = Field(50, ge=0, le=500)
    entry_order: Literal['market','limit'] = 'market'
    target_order: Literal['market','limit'] = 'market'
    limit_offset_bps: float = Field(5, ge=0, le=500)
    limit_penetration_bps: float = Field(1, ge=0, le=100)
    limit_expiry_bars: int = Field(3, ge=1, le=30)
    participation_pct: float = Field(1, gt=0, le=20)
    ema_fast: int = Field(21, ge=2, le=200)
    ema_slow: int = Field(50, ge=3, le=500)
    atr_length: int = Field(14, ge=2, le=100)
    rsi_length: int = Field(14, ge=2, le=100)
    htf_filter: Literal['off','1h','4h','both'] = 'both'
    htf_ema: int = Field(50, ge=3, le=200)
    stop_atr: float = Field(1.5, ge=.2, le=10)
    reward_risk: float = Field(2, ge=.2, le=10)
    max_hold_hours: float = Field(8, ge=.083333, le=72)
    trailing_atr: float = Field(0, ge=0, le=10)
    breakeven_r: float = Field(0, ge=0, le=10)
    breakout_lookback: int = Field(20, ge=3, le=200)
    retest_bars: int = Field(5, ge=1, le=30)
    retest_atr: float = Field(.25, ge=0, le=2)
    vwap_band_atr: float = Field(1, ge=.1, le=5)
    range_threshold: float = Field(.8, ge=.1, le=5)
    rsi_oversold: float = Field(35, ge=5, le=49)
    rsi_overbought: float = Field(65, ge=51, le=95)
    # User-supplied historical contract rules; base asset units, not contracts.
    rules: dict[str,dict[str,float]] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_settings(self):
        import re
        self.pairs = list(dict.fromkeys(p.upper().replace('/','') for p in self.pairs))
        if any(not re.fullmatch(r'[A-Z0-9]{2,20}USDT',p) for p in self.pairs):
            raise ValueError('Pairs must be USDT symbols, e.g. JTOUSDT.')
        if self.end <= self.start: raise ValueError('End date must follow start; end is exclusive.')
        if (self.end-self.start).days>1096: raise ValueError('Maximum range is three years per run.')
        if self.end>date.today(): raise ValueError('End date cannot be in the future.')
        if self.ema_fast>=self.ema_slow: raise ValueError('Fast EMA must be shorter than slow EMA.')
        if self.execution_minutes>self.timeframe: raise ValueError('Execution resolution must not exceed signal resolution.')
        if self.maintenance_margin_pct/100+self.liquidation_fee_bps/10000>=1/self.leverage:
            raise ValueError('Leverage leaves no liquidation buffer under the selected maintenance assumptions.')
        for pair,rule in self.rules.items():
            if set(rule)-{'tick_size','qty_step','min_notional','min_qty','maintenance_margin_pct'}:
                raise ValueError(f'Unknown rule for {pair}.')
            if any(not isinstance(v,(float,int)) or not 0<v<1e12 for v in rule.values()):
                raise ValueError('Contract rules must be positive finite numbers.')
        return self

STRATEGIES = {
 'trend_pullback': {'name':'Trend pullback','rule':'Fast EMA above slow; candle tests fast EMA and closes back above with a bullish body. Shorts mirror. Selected completed higher-timeframe EMA gates apply.'},
 'breakout_retest': {'name':'Breakout & retest','rule':'Close breaks a prior N-bar high/low. A later candle retests the fixed breakout level within tolerance and closes back through it before expiry. Higher-timeframe gates apply.'},
 'vwap_reversion': {'name':'VWAP mean reversion','rule':'Daily UTC VWAP deviation exceeds ATR band; RSI is extreme; EMA separation/ATR is below the range threshold; candle body turns toward VWAP. Higher-timeframe gates apply if enabled.'},
}
