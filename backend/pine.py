"""Pine Script v5/v6 strategies, run one completed candle at a time.

A pasted script is not translated into Python. It is parsed and then executed bar
by bar on the same completed signal candles every other strategy sees, so at bar N
it can only read bars up to N: look-ahead is impossible by construction (AGENTS.md,
invariant 1). Its orders become a plan frame for the exact engine, which decides
fills, costs, funding and liquidation exactly as it does for everything else.

Anything the engine cannot honour is refused with a line number instead of being
approximated silently. Rules and limits: docs/PINE-CONVERSION.md.
"""
import hashlib, json, math, re, time, uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from .config import Config
from .strategies import REGISTRY, features, register_strategy

NAN = float('nan')
LOOP_LIMIT = 20_000      # Loop iterations per candle, all loops together. A runaway loop is an error, not a hang.
CHECK_SECONDS = 20       # A check runs in the request; a script that cannot finish its trial run in this time is refused.
CACHE_SIZE = 6           # Plans kept per script: a grid reruns the same frame with settings the script never reads.


class PineError(ValueError):
    def __init__(self, text, line=None):
        super().__init__(f'Line {line}: {text}' if line else text); self.text = text; self.line = line


class _Break(Exception): pass
class _Continue(Exception): pass


def isna(x): return x is None or x != x
def truth(x): return bool(x) and x == x     # na is false in a condition, as in Pine.


# ---------------------------------------------------------------- lexer

KEYWORDS = {'if', 'else', 'for', 'to', 'by', 'in', 'while', 'switch', 'var', 'varip', 'and', 'or', 'not', 'true', 'false',
            'break', 'continue', 'import', 'export', 'method', 'type', 'enum'}
TOKEN = re.compile(r'''(?P<ws>[ \t]+)|(?P<comment>//.*)|(?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)|(?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''
                   r'''|(?P<color>\#[0-9a-fA-F]{6,8})|(?P<id>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)|(?P<op>:=|==|!=|<=|>=|=>|\+=|-=|\*=|/=|%=|[-+*/%<>=?:,()\[\].])''')
ESCAPES = {'n': '\n', 't': '\t'}


def scan(text, line):
    out = []; pos = 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m: raise PineError(f'unexpected character {text[pos]!r}.', line)
        pos = m.end(); kind = m.lastgroup; value = m.group()
        if kind in ('ws', 'comment'): continue
        if kind == 'num': value = float(value) if any(x in value for x in '.eE') else int(value)
        elif kind == 'str': value = re.sub(r'\\(.)', lambda x: ESCAPES.get(x.group(1), x.group(1)), value[1:-1])
        elif kind == 'id' and value in KEYWORDS: kind = 'kw'
        out.append((kind, value, line))
    return out


def lex(source):
    """Tokens with Python-style nl / indent / dedent markers, and the declared //@version."""
    tokens = []; depth = 0; levels = [0]; version = None
    for number, raw in enumerate(source.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
        declared = re.match(r'\s*//\s*@version\s*=\s*(\d+)', raw)
        if declared: version = int(declared.group(1))
        text = raw.expandtabs(4); found = scan(text, number)
        if not found: continue
        indent = len(text) - len(text.lstrip(' '))
        # Pine wraps a statement inside brackets, or onto a line whose indent is not a multiple of four.
        if depth == 0 and not (tokens and indent % 4):
            if tokens: tokens.append(('nl', '', number))
            level = indent // 4
            if level > levels[-1]: levels.append(level); tokens.append(('indent', '', number))
            while level < levels[-1]: levels.pop(); tokens.append(('dedent', '', number))
            if level != levels[-1]: raise PineError('indentation does not match any open block.', number)
        tokens.extend(found)
        depth = max(0, depth + sum((t[1] in ('(', '[')) - (t[1] in (')', ']')) for t in found if t[0] == 'op'))
    last = tokens[-1][2] if tokens else 1
    tokens.append(('nl', '', last)); tokens.extend(('dedent', '', last) for _ in levels[1:]); tokens.append(('eof', '', last))
    return tokens, version


def digest(tokens):
    """Identity of a script's logic: comments, blank lines and spacing do not change it; indentation does."""
    return hashlib.sha256('\x1f'.join(f'{k}:{v!r}' for k, v, _ in tokens).encode()).hexdigest()


# ---------------------------------------------------------------- parser

class Node:
    __slots__ = ('k', 'line', 'a', 'b', 'c', 'd')
    def __init__(self, k, line, a=None, b=None, c=None, d=None): self.k, self.line, self.a, self.b, self.c, self.d = k, line, a, b, c, d


TYPES = {'int', 'float', 'bool', 'string', 'color', 'series', 'simple', 'const', 'label', 'line', 'box', 'table', 'linefill', 'polyline'}
UNSUPPORTED_KW = {
    'import': 'Libraries (import) are not supported: the imported code is not part of the script.',
    'export': 'Library scripts (export) cannot be tested; paste a strategy.',
    'method': 'User-defined methods are not supported yet.',
    'type': 'User-defined types are not supported yet.',
    'enum': 'Enums are not supported yet.',
    'varip': 'varip keeps state between ticks of a live candle. That does not exist in history, so it cannot be backtested honestly.',
}
UNSUPPORTED_ROOT = {
    'array': 'Arrays are not supported yet.', 'matrix': 'Matrices are not supported yet.', 'map': 'Maps are not supported yet.',
    'ticker': 'ticker.* builds another symbol or chart type to request. Only the market being tested is loaded.',
}


WORDS = {'nl': 'the end of the line', 'indent': 'an indented block', 'dedent': 'the end of the block', 'eof': 'the end of the script', 'id': 'a name'}


def describe(t): return WORDS.get(t[0], repr(t[1]))


class Parser:
    def __init__(self, tokens): self.t = tokens; self.p = 0
    def peek(self, k=0): return self.t[min(self.p + k, len(self.t) - 1)]
    def at(self, kind, value=None, k=0): t = self.peek(k); return t[0] == kind and (value is None or t[1] == value)

    def take(self, kind=None, value=None):
        t = self.peek()
        if kind and not self.at(kind, value): raise PineError(f'expected {repr(value) if value else WORDS.get(kind, kind)}, found {describe(t)}.', t[2])
        self.p += 1; return t

    def accept(self, kind, value=None):
        if self.at(kind, value): self.p += 1; return True
        return False

    def end(self):
        if self.accept('op', ','): return     # Pine allows several statements on one line, separated by commas.
        if not (self.at('dedent') or self.at('eof')): self.take('nl')

    def statements(self, stop):
        out = []
        while not self.at(stop) and not self.at('eof'):
            if not self.accept('nl'): out.append(self.statement())
        return out

    def program(self): return self.statements('eof')
    def block(self): self.take('nl'); self.take('indent'); out = self.statements('dedent'); self.accept('dedent'); return out

    def statement(self):
        t = self.peek(); line = t[2]
        if t[0] == 'kw':
            if t[1] == 'if': return self.if_()
            if t[1] == 'switch': return self.switch_()
            if t[1] == 'for': return self.for_()
            if t[1] == 'while': self.take(); cond = self.expr(); return Node('while', line, cond, self.block())
            if t[1] in ('break', 'continue'): self.take(); self.end(); return Node(t[1], line)
            if t[1] in ('var', 'varip'): self.take(); return self.declaration(line, t[1])
            if t[1] in UNSUPPORTED_KW: raise PineError(UNSUPPORTED_KW[t[1]], line)
        if t[0] == 'op' and t[1] == '[' and self.tuple_target(): return self.declaration(line, '')
        if t[0] == 'id':
            if self.funcdef_ahead(): return self.funcdef(line)
            k = 0
            while self.peek(k)[1] in TYPES and self.at('id', k=k) and self.at('id', k=k + 1): k += 1
            if self.at('id', k=k) and self.at('op', '=', k=k + 1): return self.declaration(line, '')
            if self.at('op', k=1) and self.peek(1)[1] in (':=', '+=', '-=', '*=', '/=', '%='):
                name = self.take()[1]; op = self.take()[1]; return Node('assign', line, name, op, self.value())
        e = self.expr(); self.end(); return Node('expr', line, e)

    def declaration(self, line, mode):
        while self.peek()[1] in TYPES and self.at('id') and (self.at('id', k=1) or self.at('op', '[', k=1)): self.take()
        if self.accept('op', '['):
            names = [self.take('id')[1]]
            while self.accept('op', ','): names.append(self.take('id')[1])
            self.take('op', ']'); self.take('op', '='); return Node('tdecl', line, names, self.value())
        name = self.take('id')[1]; self.take('op', '='); return Node('decl', line, name, self.value(), mode)

    def value(self):
        """A right-hand side. `if` and `switch` may stand there and yield the value of the branch that ran."""
        if self.at('kw', 'if'): return self.if_()
        if self.at('kw', 'switch'): return self.switch_()
        if self.at('kw', 'for') or self.at('kw', 'while'): raise PineError('a loop used as a value is not supported.', self.peek()[2])
        e = self.expr(); self.end(); return e

    def tuple_target(self):
        k = 1
        while self.at('id', k=k):
            if self.at('op', ']', k=k + 1): return self.at('op', '=', k=k + 2)
            if not self.at('op', ',', k=k + 1): return False
            k += 2
        return False

    def funcdef_ahead(self):
        if not self.at('op', '(', k=1): return False
        depth = 0; k = 1
        while self.peek(k)[0] not in ('eof', 'nl'):
            v = self.peek(k)[1] if self.peek(k)[0] == 'op' else ''
            depth += (v in ('(', '[')) - (v in (')', ']'))
            if depth == 0: return self.at('op', '=>', k=k + 1)
            k += 1
        return False

    def funcdef(self, line):
        name = self.take('id')[1]; self.take('op', '('); params = []
        while not self.at('op', ')'):
            while self.at('id') and self.at('id', k=1): self.take()     # Type words before the parameter name.
            p = self.take('id')[1]; params.append((p, self.expr() if self.accept('op', '=') else None))
            if not self.accept('op', ','): break
        self.take('op', ')'); self.take('op', '=>')
        return Node('func', line, name, params, self.block() if self.at('nl') else [self.statement()])

    def if_(self):
        line = self.take('kw', 'if')[2]; cond = self.expr(); then = self.block(); other = None
        if self.accept('kw', 'else'): other = [self.if_()] if self.at('kw', 'if') else self.block()
        return Node('if', line, cond, then, other)

    def switch_(self):
        line = self.take()[2]; subject = None if self.at('nl') else self.expr()
        self.take('nl'); self.take('indent'); arms = []
        while not self.at('dedent') and not self.at('eof'):
            if self.accept('nl'): continue
            cond = None if self.at('op', '=>') else self.expr()
            self.take('op', '=>'); arms.append((cond, self.block() if self.at('nl') else [self.statement()]))
        self.accept('dedent'); return Node('switch', line, subject, arms)

    def for_(self):
        line = self.take()[2]
        if self.at('op', '[') or self.at('kw', 'in', k=1): raise PineError('for ... in loops over a collection are not supported yet.', line)
        name = self.take('id')[1]; self.take('op', '='); start = self.expr(); self.take('kw', 'to'); stop = self.expr()
        step = self.expr() if self.accept('kw', 'by') else None
        return Node('for', line, name, (start, stop, step), self.block())

    def expr(self):
        cond = self.or_()
        if self.accept('op', '?'):
            a = self.expr(); self.take('op', ':'); return Node('tern', cond.line, cond, a, self.expr())
        return cond

    def chain(self, sub, kind, values):
        left = sub()
        while self.peek()[0] == kind and self.peek()[1] in values:
            t = self.take(); left = Node('bin', t[2], t[1], left, sub())
        return left

    def or_(self): return self.chain(self.and_, 'kw', ('or',))
    def and_(self): return self.chain(self.equality, 'kw', ('and',))
    def equality(self): return self.chain(self.comparison, 'op', ('==', '!='))
    def comparison(self): return self.chain(self.additive, 'op', ('<', '<=', '>', '>='))
    def additive(self): return self.chain(self.multiplicative, 'op', ('+', '-'))
    def multiplicative(self): return self.chain(self.unary, 'op', ('*', '/', '%'))

    def unary(self):
        t = self.peek()
        if (t[0] == 'op' and t[1] in ('-', '+')) or (t[0] == 'kw' and t[1] == 'not'): self.take(); return Node('un', t[2], t[1], self.unary())
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            if self.at('op', '(') and e.k == 'name':
                line = self.take()[2]; args = []; named = {}
                while not self.at('op', ')'):
                    if self.at('id') and self.at('op', '=', k=1): key = self.take()[1]; self.take(); named[key] = self.expr()
                    else: args.append(self.expr())
                    if not self.accept('op', ','): break
                self.take('op', ')'); e = Node('call', line, e.a, args, named)
            elif self.at('op', '['): self.take(); offset = self.expr(); self.take('op', ']'); e = Node('sub', e.line, e, offset)
            elif self.at('op', '.'): raise PineError('calling a method on a value (value.method()) is not supported yet.', self.peek()[2])
            else: return e

    def primary(self):
        t = self.take(); kind, value, line = t
        if kind == 'num' or kind == 'str': return Node('const', line, value)
        if kind == 'color': return Node('const', line, None)
        if kind == 'kw' and value in ('true', 'false'): return Node('const', line, value == 'true')
        if kind == 'id': return Node('name', line, value)
        if kind == 'op' and value == '(': e = self.expr(); self.take('op', ')'); return e
        if kind == 'op' and value == '[':
            items = [self.expr()]
            while self.accept('op', ','): items.append(self.expr())
            self.take('op', ']'); return Node('tuple', line, items)
        raise PineError(UNSUPPORTED_KW.get(value) if kind == 'kw' and value in UNSUPPORTED_KW else f'unexpected {describe(t)}.', line)


# ---------------------------------------------------------------- indicator state
# One instance per place in the script that calls it, updated only on bars where that
# call runs. That is Pine's own model, so a function inside an `if` behaves as it does there.

def length(n):
    if isna(n) or n < 1: raise PineError('a length must be a whole number of at least 1.')
    return int(n)


class Buffer:
    def __init__(self): self.b = []
    def push(self, x, keep):
        b = self.b; b.append(x)
        if len(b) > keep * 4 + 64: del b[:-keep]
        return b


class Sma(Buffer):
    def step(self, x, n): n = length(n); b = self.push(x, n); return sum(b[-n:]) / n if len(b) >= n else NAN


class Ema:
    """Seeded with the simple average of the first full window, then recursive: Pine's definition."""
    alpha = staticmethod(lambda n: 2 / (n + 1))
    def __init__(self): self.seed = Sma(); self.v = NAN
    def step(self, x, n):
        n = length(n); seed = self.seed.step(x, n); a = self.alpha(n)
        self.v = seed if self.v != self.v else a * x + (1 - a) * self.v
        return self.v


class Rma(Ema): alpha = staticmethod(lambda n: 1 / n)


class Wma(Buffer):
    def step(self, x, n):
        n = length(n); b = self.push(x, n)
        return sum(v * (i + 1) for i, v in enumerate(b[-n:])) / (n * (n + 1) / 2) if len(b) >= n else NAN


class Hma:
    def __init__(self): self.half = Wma(); self.full = Wma(); self.out = Wma()
    def step(self, x, n): n = length(n); return self.out.step(2 * self.half.step(x, max(n // 2, 1)) - self.full.step(x, n), max(int(math.sqrt(n)), 1))


class Vwma:
    def __init__(self): self.pv = Sma(); self.v = Sma()
    def step(self, x, n, volume): return div(self.pv.step(x * volume, n), self.v.step(volume, n))


class Swma(Buffer):
    def step(self, x): b = self.push(x, 4); return (b[-4] + 2 * b[-3] + 2 * b[-2] + b[-1]) / 6 if len(b) >= 4 else NAN


class Alma(Buffer):
    def step(self, x, n, offset, sigma, floor=False):
        n = length(n); b = self.push(x, n)
        if len(b) < n: return NAN
        m = offset * (n - 1); m = math.floor(m) if truth(floor) else m; s = n / sigma
        w = [math.exp(-(i - m) ** 2 / (2 * s * s)) for i in range(n)]
        return sum(v * k for v, k in zip(b[-n:], w)) / sum(w)


class Linreg(Buffer):
    def step(self, x, n, offset=0):
        n = length(n); b = self.push(x, n)
        if len(b) < n: return NAN
        ys = b[-n:]; mx = (n - 1) / 2; my = sum(ys) / n; sxx = sum((i - mx) ** 2 for i in range(n))
        slope = sum((i - mx) * (y - my) for i, y in enumerate(ys)) / sxx if sxx else 0.
        return my - slope * mx + slope * (n - 1 - offset)


class Stdev(Buffer):
    def step(self, x, n, biased=True):
        n = length(n); b = self.push(x, n)
        if len(b) < n or (n == 1 and not truth(biased)): return NAN
        w = b[-n:]; mean = sum(w) / n
        return self.finish(sum((v - mean) ** 2 for v in w) / (n if truth(biased) else n - 1))
    finish = staticmethod(lambda v: math.sqrt(v) if v == v else NAN)


class Variance(Stdev): finish = staticmethod(lambda v: v)


class Dev(Buffer):
    def step(self, x, n):
        n = length(n); b = self.push(x, n)
        if len(b) < n: return NAN
        w = b[-n:]; mean = sum(w) / n; return sum(abs(v - mean) for v in w) / n


class Extreme(Buffer):
    """Highest or lowest of the last n values, ignoring na; `bars` returns the offset to it instead (0 or negative)."""
    pick = max; bars = False
    def step(self, x, n):
        n = length(n); b = self.push(x, n)
        if len(b) < n: return NAN
        w = [v for v in b[-n:] if v == v]
        if not w: return NAN
        best = self.pick(w)
        if not self.bars: return best
        return -next(i for i in range(n) if b[-1 - i] == best)


class Highest(Extreme): pass
class Lowest(Extreme): pick = min
class HighestBars(Extreme): bars = True
class LowestBars(Extreme): pick = min; bars = True


class Change(Buffer):
    scale = staticmethod(lambda x, p: x - p)
    def step(self, x, n=1):
        n = length(n); b = self.push(x, n + 1)
        if len(b) <= n: return False if isinstance(x, bool) else NAN
        p = b[-1 - n]
        return x != p if isinstance(x, bool) else self.scale(x, p)


class Roc(Change): scale = staticmethod(lambda x, p: div(100 * (x - p), p))


class Cross:
    way = 0
    def __init__(self): self.p = (NAN, NAN)
    def step(self, a, b):
        pa, pb = self.p; self.p = (a, b)
        over = a > b and pa <= pb; under = a < b and pa >= pb
        return over if self.way == 1 else under if self.way == -1 else over or under


class Crossover(Cross): way = 1
class Crossunder(Cross): way = -1


class Rsi:
    def __init__(self): self.p = NAN; self.up = Rma(); self.down = Rma()
    def step(self, x, n):
        ch = x - self.p; self.p = x
        up = self.up.step(max(ch, 0.) if ch == ch else NAN, n); down = self.down.step(max(-ch, 0.) if ch == ch else NAN, n)
        if up != up or down != down: return NAN
        return 100. if down == 0 else 0. if up == 0 else 100 - 100 / (1 + up / down)


class Atr(Rma): pass


class Stoch:
    def __init__(self): self.hi = Highest(); self.lo = Lowest()
    def step(self, x, high, low, n): hh = self.hi.step(high, n); ll = self.lo.step(low, n); return div(100 * (x - ll), hh - ll)


class Bands:
    def __init__(self): self.mid = Sma(); self.dev = Stdev()
    def step(self, x, n, mult): basis = self.mid.step(x, n); d = mult * self.dev.step(x, n); return [basis, basis + d, basis - d]


class Keltner:
    def __init__(self): self.mid = Ema(); self.span = Ema()
    def step(self, x, n, mult, use_true_range, true_range, high_low):
        basis = self.mid.step(x, n); d = mult * self.span.step(true_range if truth(use_true_range) else high_low, n)
        return [basis, basis + d, basis - d]


class Macd:
    def __init__(self): self.fast = Ema(); self.slow = Ema(); self.signal = Ema()
    def step(self, x, fast, slow, signal):
        line = self.fast.step(x, fast) - self.slow.step(x, slow); s = self.signal.step(line, signal); return [line, s, line - s]


class Cci:
    def __init__(self): self.mid = Sma(); self.dev = Dev()
    def step(self, x, n): return div(x - self.mid.step(x, n), .015 * self.dev.step(x, n))


class BarsSince:
    def __init__(self): self.n = None
    def step(self, cond):
        if truth(cond): self.n = 0
        elif self.n is not None: self.n += 1
        return NAN if self.n is None else self.n


class ValueWhen(Buffer):
    def step(self, cond, x, occurrence=0):
        k = int(occurrence)
        if truth(cond): self.push(x, k + 1)
        return self.b[-1 - k] if len(self.b) > k else NAN


class Rising(Buffer):
    better = staticmethod(lambda x, v: x > v)
    def step(self, x, n): n = length(n); b = self.push(x, n + 1); return len(b) > n and all(self.better(x, v) for v in b[-n - 1:-1])


class Falling(Rising): better = staticmethod(lambda x, v: x < v)


class Cum:
    def __init__(self): self.t = 0.
    def step(self, x):
        if x == x: self.t += x
        return self.t


class RollSum(Buffer):
    def step(self, x, n): n = length(n); b = self.push(x, n); return sum(b[-n:]) if len(b) >= n else NAN


class FixNan:
    def __init__(self): self.v = NAN
    def step(self, x):
        if not isna(x): self.v = x
        return self.v


class Pivot(Buffer):
    """A swing point confirmed `right` bars later. Bars before it may equal it; bars after it may not."""
    sign = 1
    def step(self, x, left, right):
        left = int(left); right = int(right); n = left + right + 1; b = self.push(x, n)
        if len(b) < n: return NAN
        w = b[-n:]; p = w[left]; s = self.sign
        if p != p or any(v * s > p * s for v in w[:left]) or any(v * s >= p * s for v in w[left + 1:]): return NAN
        return p


class PivotLow(Pivot): sign = -1


# ---------------------------------------------------------------- plain functions

def div(a, b):
    try: return a / b
    except ZeroDivisionError: return NAN


def div5(a, b):
    """Pine v5 keeps a whole-number result when both sides are whole numbers; v6 does not."""
    if type(a) is int and type(b) is int: return int(a / b) if b else NAN
    return div(a, b)


def mod(a, b):
    try: r = math.fmod(a, b)
    except (ValueError, ZeroDivisionError): return NAN
    return int(r) if type(a) is int and type(b) is int else r


def ne(a, b): return False if isna(a) or isna(b) else a != b
def nz(x, y=0): return y if isna(x) else x
def any_na(xs): return any(isna(x) for x in xs)
def pmax(*xs): return NAN if any_na(xs) else max(xs)
def pmin(*xs): return NAN if any_na(xs) else min(xs)
def pavg(*xs): return sum(xs) / len(xs)
def pround(x, precision=None):
    if isna(x): return NAN
    if precision is None: return math.floor(x + .5)
    k = 10 ** int(precision); return math.floor(x * k + .5) / k
def guard(fn):
    def safe(*xs):
        if any_na(xs): return NAN
        try: return fn(*xs)
        except (ValueError, OverflowError, ZeroDivisionError): return NAN
    return safe
def to_int(x): return NAN if isna(x) else int(x)
def to_float(x): return NAN if isna(x) else float(x)
def to_string(x, fmt=None):
    if isna(x): return 'NaN'
    return ('true' if x else 'false') if isinstance(x, bool) else str(x)
def str_format(fmt, *xs): return re.sub(r'\{(\d+)[^}]*\}', lambda m: to_string(xs[int(m.group(1))]) if int(m.group(1)) < len(xs) else '', str(fmt))

MINUTES = {'D': 1440, '1D': 1440}


def timeframe_minutes(tf, line=None):
    tf = str(tf)
    if tf.isdigit(): return int(tf)
    if tf in MINUTES: return MINUTES[tf]
    raise PineError(f'timeframe "{tf}" is not supported; use minutes or "D".', line)


def timestamp(*a):
    """Milliseconds UTC, from a date string or year, month, day[, hour, minute, second], optionally led by a time zone."""
    zone = 'UTC'
    if a and isinstance(a[0], str):
        if len(a) == 1:
            t = pd.Timestamp(a[0]); return int((t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')).timestamp() * 1000)
        zone = a[0]; a = a[1:]
    m = re.fullmatch(r'(?:GMT|UTC)([+-]\d{1,2})(?::?(\d{2}))?', zone)
    if m: zone = timezone(pd.Timedelta(hours=int(m.group(1)), minutes=int(m.group(2) or 0) * (-1 if m.group(1)[0] == '-' else 1)))
    elif zone in ('GMT', 'UTC', 'Etc/UTC', ''): zone = 'UTC'
    y, mo, d, h, mi, s = (list(a) + [0, 0, 0])[:6] if len(a) >= 3 else (NAN,) * 6
    if any_na((y, mo, d, h, mi, s)): return NAN
    # Pine accepts out-of-range parts (month 13, day 0) and rolls them over; so does this.
    base = pd.Timestamp(year=int(y), month=1, day=1, tz=zone) + pd.DateOffset(months=int(mo) - 1)
    return int((base + pd.Timedelta(days=int(d) - 1, hours=int(h), minutes=int(mi), seconds=int(s))).timestamp() * 1000)


PRELUDE = '''
ta.supertrend(factor, atrPeriod) =>
    src = hl2
    atr = ta.atr(atrPeriod)
    upperBand = src + factor * atr
    lowerBand = src - factor * atr
    prevLowerBand = nz(lowerBand[1])
    prevUpperBand = nz(upperBand[1])
    lowerBand := lowerBand > prevLowerBand or close[1] < prevLowerBand ? lowerBand : prevLowerBand
    upperBand := upperBand < prevUpperBand or close[1] > prevUpperBand ? upperBand : prevUpperBand
    int _direction = na
    float superTrend = na
    prevSuperTrend = superTrend[1]
    if na(atr[1])
        _direction := 1
    else if prevSuperTrend == prevUpperBand
        _direction := close > upperBand ? -1 : 1
    else
        _direction := close < lowerBand ? 1 : -1
    superTrend := _direction == -1 ? lowerBand : upperBand
    [superTrend, _direction]
ta.dmi(diLength, adxSmoothing) =>
    up = ta.change(high)
    down = -ta.change(low)
    plusDM = na(up) ? na : (up > down and up > 0 ? up : 0)
    minusDM = na(down) ? na : (down > up and down > 0 ? down : 0)
    trur = ta.rma(ta.tr, diLength)
    plus = fixnan(100 * ta.rma(plusDM, diLength) / trur)
    minus = fixnan(100 * ta.rma(minusDM, diLength) / trur)
    total = plus + minus
    adx = 100 * ta.rma(math.abs(plus - minus) / (total == 0 ? 1 : total), adxSmoothing)
    [plus, minus, adx]
ta.sar(start, inc, max) =>
    var float result = na
    var float maxMin = na
    var float acceleration = na
    var bool isBelow = false
    bool isFirstTrendBar = false
    if bar_index == 1
        if close > close[1]
            isBelow := true
            maxMin := high
            result := low[1]
        else
            isBelow := false
            maxMin := low
            result := high[1]
        isFirstTrendBar := true
        acceleration := start
    result := result + acceleration * (maxMin - result)
    if isBelow
        if result > low
            isFirstTrendBar := true
            isBelow := false
            result := math.max(high, maxMin)
            maxMin := low
            acceleration := start
    else
        if result < high
            isFirstTrendBar := true
            isBelow := true
            result := math.min(low, maxMin)
            maxMin := high
            acceleration := start
    if not isFirstTrendBar
        if isBelow
            if high > maxMin
                maxMin := high
                acceleration := math.min(acceleration + inc, max)
        else
            if low < maxMin
                maxMin := low
                acceleration := math.min(acceleration + inc, max)
    if isBelow
        result := math.min(result, low[1])
        if bar_index > 1
            result := math.min(result, low[2])
    else
        result := math.max(result, high[1])
        if bar_index > 1
            result := math.max(result, high[2])
    result
ta.mfi(series, length) =>
    change = ta.change(series)
    upper = math.sum(volume * (change <= 0.0 ? 0.0 : series), length)
    lower = math.sum(volume * (change >= 0.0 ? 0.0 : series), length)
    100.0 - 100.0 / (1.0 + upper / lower)
ta.cmo(series, length) =>
    momentum = ta.change(series)
    gains = math.sum(momentum >= 0 ? momentum : 0.0, length)
    losses = math.sum(momentum >= 0 ? 0.0 : -momentum, length)
    100 * (gains - losses) / (gains + losses)
ta.tsi(source, short_length, long_length) =>
    momentum = ta.change(source)
    ta.ema(ta.ema(momentum, long_length), short_length) / ta.ema(ta.ema(math.abs(momentum), long_length), short_length)
ta.wpr(length) =>
    top = ta.highest(high, length)
    100 * (close - top) / (top - ta.lowest(low, length))
'''
PRELUDE_AST = Parser(lex(PRELUDE)[0]).program()


# ---------------------------------------------------------------- what a script uses

SERIES = {'open': 'O', 'high': 'H', 'low': 'L', 'close': 'C', 'volume': 'V', 'hl2': 'HL2', 'hlc3': 'HLC3', 'ohlc4': 'OHLC4', 'hlcc4': 'HLCC4',
          'time': 'T', 'time_close': 'TC', 'ta.tr': 'TR', 'ta.vwap': 'VWAP', 'ta.obv': 'OBV', 'year': 'YEAR', 'month': 'MONTH',
          'dayofmonth': 'DAY', 'dayofweek': 'WEEKDAY', 'hour': 'HOUR', 'minute': 'MINUTE', 'second': 'SECOND', 'weekofyear': 'WEEK'}
CONSTANTS = {'na': NAN, 'strategy.long': 'long', 'strategy.short': 'short', 'math.pi': math.pi, 'math.e': math.e, 'math.phi': (1 + 5 ** .5) / 2,
             'math.rphi': 2 / (1 + 5 ** .5), 'barstate.isconfirmed': True, 'barstate.ishistory': True, 'barstate.isrealtime': False,
             'barstate.isnew': True, 'timeframe.isintraday': True, 'timeframe.isminutes': True, 'timeframe.isseconds': False,
             'timeframe.isticks': False, 'timeframe.isdaily': False, 'timeframe.isweekly': False, 'timeframe.ismonthly': False,
             'timeframe.isdwm': False, 'syminfo.timezone': 'Etc/UTC', 'syminfo.currency': 'USDT', 'syminfo.type': 'crypto',
             'syminfo.ticker': '', 'syminfo.tickerid': '', 'syminfo.root': '', 'syminfo.prefix': '', 'syminfo.basecurrency': '',
             'syminfo.description': '', 'syminfo.session': '24x7', 'dayofweek.sunday': 1, 'dayofweek.monday': 2, 'dayofweek.tuesday': 3,
             'dayofweek.wednesday': 4, 'dayofweek.thursday': 5, 'dayofweek.friday': 6, 'dayofweek.saturday': 7}
ACCOUNT = 'reads the account (equity, profit or win counts). The engine computes those with its own costs and sizing, so a script cannot depend on them here.'
REFUSED_NAMES = {
    'timenow': 'timenow is the wall clock of a live chart. It has no meaning in history.',
    'last_bar_index': 'last_bar_index tells the script where history ends, which is information from the future.',
    'last_bar_time': 'last_bar_time tells the script where history ends, which is information from the future.',
    'syminfo.mintick': 'syminfo.mintick is not available to a strategy here. Use a price or percentage offset instead.',
    'syminfo.pointvalue': 'syminfo.pointvalue is not available to a strategy here.',
    **{f'strategy.{k}': f'strategy.{k} {ACCOUNT}' for k in ['equity', 'netprofit', 'openprofit', 'initial_capital', 'grossprofit', 'grossloss',
       'max_drawdown', 'max_runup', 'wintrades', 'losstrades', 'eventrades', 'margin_liquidation_price', 'netprofit_percent',
       'openprofit_percent', 'max_drawdown_percent', 'avg_trade', 'avg_winning_trade', 'avg_losing_trade']},
}
POSITION = {'strategy.position_size': lambda m: float(m.pos), 'strategy.position_avg_price': lambda m: m.avg if m.pos else NAN,
            'strategy.opentrades': lambda m: 1 if m.pos else 0, 'strategy.closedtrades': lambda m: m.closed_trades,
            'strategy.position_entry_name': lambda m: m.entry_id if m.pos else NAN}
VISUAL_ROOTS = {'color', 'shape', 'location', 'size', 'plot', 'line', 'label', 'box', 'table', 'position', 'text', 'display', 'format', 'xloc',
                'yloc', 'extend', 'hline', 'font', 'alert', 'scale', 'currency', 'barmerge', 'linefill', 'polyline', 'chart', 'order',
                'adjustment', 'session', 'log'}
VISUAL_CALLS = {'plot', 'plotshape', 'plotchar', 'plotarrow', 'plotcandle', 'plotbar', 'hline', 'fill', 'bgcolor', 'barcolor', 'alert',
                'alertcondition', 'max_bars_back'}
STRATEGY_CONSTANT = re.compile(r'strategy\.(fixed|cash|percent_of_equity|commission\.\w+|oca\.\w+|direction\.\w+)$')

ENTRY = {5: 'id,direction,qty,limit,stop,oca_name,oca_type,comment,when,alert_message,disable_alert',
         6: 'id,direction,qty,limit,stop,oca_name,oca_type,comment,alert_message,disable_alert'}
CLOSE = {5: 'id,when,comment,qty,qty_percent,alert_message,immediately,disable_alert', 6: 'id,comment,qty,qty_percent,alert_message,immediately,disable_alert'}
CLOSE_ALL = {5: 'when,comment,alert_message,immediately,disable_alert', 6: 'comment,alert_message,immediately,disable_alert'}
EXIT = ('id,from_entry,qty,qty_percent,profit,limit,loss,stop,trail_price,trail_points,trail_offset,oca_name,comment,when,comment_profit,'
        'comment_loss,comment_trailing,alert_message,alert_profit,alert_loss,alert_trailing,disable_alert')


class Report:
    """What a script asks for, sorted by how faithfully it can be honoured."""
    def __init__(self):
        self.version = None; self.title = None; self.refused = []; self.approximated = OrderedDict(); self.ignored = OrderedDict()
        self.inputs = []; self.uses = set(); self.entries = OrderedDict(); self.exits = OrderedDict(); self.declared = False; self.orders = 0

    def refuse(self, text, line=None):
        if not any(x['text'] == text and x['line'] == line for x in self.refused): self.refused.append({'line': line, 'text': text})

    def approx(self, key, text, line=None): self._note(self.approximated, key, text, line)
    def ignore(self, key, text, line=None): self._note(self.ignored, key, text, line)

    @staticmethod
    def _note(store, key, text, line):
        entry = store.setdefault(key, {'text': text, 'lines': []})
        if line and line not in entry['lines']: entry['lines'].append(line)

    def out(self, **extra):
        self.refused.sort(key=lambda x: x['line'] or 0)
        return {'ok': not self.refused, 'version': self.version, 'title': self.title, 'refused': self.refused,
                'approximated': list(self.approximated.values()), 'ignored': list(self.ignored.values()), 'inputs': self.inputs,
                'indicators': sorted(self.uses), 'entries': [{'id': k, 'direction': v} for k, v in self.entries.items()],
                'exits': list(self.exits.values()), **extra}


# ---------------------------------------------------------------- compiler

class Layout:
    """Storage one function (or the script body) needs: variable slots, per-call-site state, and which slots keep history."""
    def __init__(self): self.n = 0; self.states = 0; self.hist = set()
    def slot(self): self.n += 1; return self.n - 1
    def state(self): self.states += 1; return self.states - 1


class Frame:
    __slots__ = ('v', 's', 'h')
    def __init__(self, layout): self.v = [NAN] * layout.n; self.s = [None] * layout.states; self.h = {k: [] for k in layout.hist}


BUILTINS = {}


def builtin(*names):
    def register(fn):
        for name in names: BUILTINS[name] = fn
        return fn
    return register


def literal(node):
    """The value of a constant argument, or Ellipsis if it is not constant."""
    if node is None: return ...
    if node.k == 'const': return node.a
    if node.k == 'name' and node.a in CONSTANTS: return CONSTANTS[node.a]
    if node.k == 'un' and node.a == '-' and node.b.k == 'const' and not isinstance(node.b.a, (str, bool)): return -node.b.a
    return ...


def shown(node):
    v = literal(node)
    if v is not ...: return 'na' if isna(v) else v
    return node.a if node.k == 'name' else 'an expression'


class Compiler:
    """Turns the syntax tree into closures over one Machine. Each closure takes the frame it runs in."""
    def __init__(self, machine, version, report, capture=None):
        self.m = machine; self.version = version; self.r = report; self.layout = Layout(); self.root = self.layout; self.capture = capture
        self.scopes = [{}]; self.top = self.scopes[0]; self.funcs = {}; self.inside = False; self.quiet = True
        self.body(PRELUDE_AST); self.quiet = False     # Built-ins written in Pine: their own notes are not the script's.

    def body(self, statements): return [self.stmt(s) for s in statements]

    def refuse(self, text, line): self.r.refuse(text, line); return lambda fr: NAN

    def declare(self, name):
        slot = self.layout.slot()
        if name != '_': self.scopes[-1][name] = slot
        return slot

    def find(self, name):
        """(slot, is_global) for a visible variable, else None. A function may read script variables but owns its locals."""
        for scope in reversed(self.scopes):
            if name in scope: return scope[name], False
        if self.inside and name in self.top: return self.top[name], True
        return None

    def block(self, statements):
        self.scopes.append({}); fs = self.body(statements); self.scopes.pop()
        if not fs: return lambda fr: NAN
        if len(fs) == 1: return fs[0]
        head = fs[:-1]; last = fs[-1]
        def run(fr):
            for f in head: f(fr)
            return last(fr)
        return run

    # -- statements

    def stmt(self, n):
        k = n.k; m = self.m; line = n.line
        if k == 'expr':
            e = self.ex(n.a)
            def f(fr): m.line = line; return e(fr)
            return f
        if k == 'decl':
            if n.c == 'varip': return self.refuse(UNSUPPORTED_KW['varip'], line)
            value = self.ex(n.b); slot = self.declare(n.a)
            if n.c == 'var':
                once = self.layout.state()
                def f(fr):
                    if fr.s[once] is None: m.line = line; fr.s[once] = 1; fr.v[slot] = value(fr)
                    return fr.v[slot]
            else:
                def f(fr): m.line = line; v = fr.v[slot] = value(fr); return v
            return f
        if k == 'tdecl':
            value = self.ex(n.b); slots = [self.declare(x) for x in n.a]
            def f(fr):
                m.line = line; values = value(fr)
                if not isinstance(values, list): values = [values] * len(slots) if isna(values) else None     # No value yet: every name is na.
                if values is None or len(values) != len(slots): raise PineError(f'expected {len(slots)} values to unpack.')
                for slot, v in zip(slots, values): fr.v[slot] = v
            return f
        if k == 'assign':
            value = self.ex(n.c); found = self.find(n.a)
            if not found: return self.refuse(f'`{n.a}` is assigned before it is declared.', line)
            if found[1]: return self.refuse(f'a function cannot change the script variable `{n.a}`.', line)
            slot = found[0]; op = self.binary(n.b[0]) if n.b != ':=' else None
            if op:
                def f(fr): m.line = line; v = fr.v[slot] = op(fr.v[slot], value(fr)); return v
            else:
                def f(fr): m.line = line; v = fr.v[slot] = value(fr); return v
            return f
        if k == 'if':
            cond = self.ex(n.a); then = self.block(n.b); other = self.block(n.c) if n.c else None
            def f(fr):
                m.line = line
                if truth(cond(fr)): return then(fr)
                return other(fr) if other else NAN
            return f
        if k == 'switch':
            subject = self.ex(n.a) if n.a else None; arms = [(self.ex(c) if c is not None else None, self.block(b)) for c, b in n.b]
            def f(fr):
                m.line = line; v = subject(fr) if subject else None
                for c, b in arms:
                    if c is None or (c(fr) == v if subject else truth(c(fr))): return b(fr)
                return NAN
            return f
        if k == 'for':
            start, stop, step = (self.ex(x) if x is not None else None for x in n.b)
            self.scopes.append({}); slot = self.declare(n.a); body = self.block(n.c); self.scopes.pop()
            def f(fr):
                m.line = line; i = start(fr); last = stop(fr); result = NAN
                if isna(i) or isna(last): return result
                up = last >= i; by = abs(step(fr)) if step else 1
                if not by > 0: raise PineError('a loop step must be above zero.')
                while i <= last if up else i >= last:
                    fr.v[slot] = i; m.loops += 1
                    if m.loops > LOOP_LIMIT: raise PineError(f'loops ran more than {LOOP_LIMIT:,} times on one candle.')
                    try: result = body(fr)
                    except _Break: break
                    except _Continue: pass
                    i = i + by if up else i - by
                return result
            return f
        if k == 'while':
            cond = self.ex(n.a); body = self.block(n.b)
            def f(fr):
                m.line = line; result = NAN
                while truth(cond(fr)):
                    m.loops += 1
                    if m.loops > LOOP_LIMIT: raise PineError(f'loops ran more than {LOOP_LIMIT:,} times on one candle.')
                    try: result = body(fr)
                    except _Break: break
                    except _Continue: pass
                return result
            return f
        if k == 'break' or k == 'continue':
            signal = _Break if k == 'break' else _Continue
            def f(fr): raise signal()
            return f
        if k == 'func':
            if self.inside or len(self.scopes) > 1: return self.refuse('functions must be defined at the top level of the script.', line)
            outer = (self.layout, self.scopes); self.layout = Layout(); self.scopes = [{}]; self.inside = True
            params = [(p, self.declare(p), default) for p, default in n.b]; body = self.block(n.c)
            self.funcs[n.a] = (self.layout, params, body); self.layout, self.scopes = outer; self.inside = False
            return lambda fr: None
        raise PineError(f'unsupported statement {k}.', line)

    # -- expressions

    def binary(self, op):
        if op == '/': return div5 if self.version == 5 else div
        return {'+': lambda a, b: a + b, '-': lambda a, b: a - b, '*': lambda a, b: a * b, '%': mod, '==': lambda a, b: a == b, '!=': ne,
                '<': lambda a, b: a < b, '<=': lambda a, b: a <= b, '>': lambda a, b: a > b, '>=': lambda a, b: a >= b}[op]

    def ex(self, n):
        k = n.k
        if k == 'const': v = n.a; return lambda fr: v
        if k == 'name': return self.name(n)
        if k == 'call': return self.call(n)
        if k == 'sub': return self.history(n)
        if k == 'tern':
            cond = self.ex(n.a); a = self.ex(n.b); b = self.ex(n.c)
            return lambda fr: a(fr) if truth(cond(fr)) else b(fr)
        if k == 'un':
            x = self.ex(n.b)
            if n.a == 'not': return lambda fr: not truth(x(fr))
            return (lambda fr: -x(fr)) if n.a == '-' else x
        if k == 'bin':
            a = self.ex(n.b); b = self.ex(n.c); op = n.a
            if op in ('and', 'or'):
                # v6 stops at the first operand that settles the answer; v5 always evaluates both, which matters
                # when the second one calls an indicator that keeps state.
                if self.version >= 6: return (lambda fr: truth(a(fr)) and truth(b(fr))) if op == 'and' else (lambda fr: truth(a(fr)) or truth(b(fr)))
                def both(fr): x = truth(a(fr)); y = truth(b(fr)); return (x and y) if op == 'and' else (x or y)
                return both
            fn = self.binary(op); return lambda fr: fn(a(fr), b(fr))
        if k == 'tuple': items = [self.ex(x) for x in n.a]; return lambda fr: [f(fr) for f in items]
        if k in ('if', 'switch'): return self.stmt(n)
        raise PineError(f'unsupported expression {k}.', n.line)

    def name(self, n):
        ident = n.a; m = self.m; found = self.find(ident)
        if found:
            slot = found[0]
            return (lambda fr: m.g.v[slot]) if found[1] else (lambda fr: fr.v[slot])
        if ident in SERIES: arr = getattr(m, SERIES[ident]); return lambda fr: arr[m.i]
        if ident == 'bar_index': return lambda fr: m.i
        if ident in CONSTANTS: v = CONSTANTS[ident]; return lambda fr: v
        if ident in POSITION:
            self.r.approx('position', 'The script reads its own position. FibStein tracks that position as TradingView would fill it: at the next open, with no costs. The engine then applies real costs and limits, so the two can differ after a refused entry or a liquidation.', n.line)
            read = POSITION[ident]; return lambda fr: read(m)
        if ident in ('barstate.islast', 'barstate.islastconfirmedhistory'):
            self.r.approx('islast', f'{ident} is always false here. Knowing which bar is the last one is information from the future.', n.line)
            return lambda fr: False
        if ident == 'barstate.isfirst': return lambda fr: m.i == 0
        if ident in ('timeframe.period', 'timeframe.multiplier'): v = str(m.tf) if ident.endswith('period') else m.tf; return lambda fr: v
        if ident in REFUSED_NAMES: return self.refuse(REFUSED_NAMES[ident], n.line)
        root = ident.split('.')[0]
        if root in UNSUPPORTED_ROOT: return self.refuse(UNSUPPORTED_ROOT[root], n.line)
        if root in VISUAL_ROOTS or STRATEGY_CONSTANT.match(ident): return lambda fr: None
        if '.' in ident and self.find(root): return self.refuse(f'`{ident}`: fields and methods on a value are not supported yet.', n.line)
        if ident in BUILTINS or ident in self.funcs: return self.refuse(f'`{ident}` is a function and needs brackets here.', n.line)
        return self.refuse(f'`{ident}` is not a variable this converter knows.', n.line)

    def offset(self, node):
        value = self.ex(node)
        def read(fr):
            k = value(fr)
            if isna(k): return None
            if k < 0: raise PineError('a negative history offset would read a bar from the future.')
            return int(k)
        return read

    def history(self, n):
        base = n.a; off = self.offset(n.b); m = self.m
        if base.k == 'name':
            found = self.find(base.a)
            if found:
                slot, shared = found; (self.root if shared else self.layout).hist.add(slot)
                def f(fr):
                    k = off(fr); fr = m.g if shared else fr
                    if not k: return NAN if k is None else fr.v[slot]
                    h = fr.h[slot]; return h[-k] if len(h) >= k else NAN
                return f
            if base.a in SERIES:
                arr = getattr(m, SERIES[base.a])
                def f(fr): k = off(fr); return NAN if k is None or k > m.i else arr[m.i - k]
                return f
            if base.a == 'bar_index':
                def f(fr): k = off(fr); return NAN if k is None else m.i - k
                return f
        value = self.ex(base); site = self.layout.state()
        def f(fr):
            st = fr.s[site]; v = value(fr)
            if st is None: st = fr.s[site] = [m.i, v, []]
            elif st[0] != m.i: st[2].append(st[1]); st[0] = m.i
            st[1] = v; k = off(fr)
            if not k: return NAN if k is None else v
            return st[2][-k] if len(st[2]) >= k else NAN
        return f

    # -- calls

    def bind(self, n, params, loose=False):
        """A call's arguments by parameter name."""
        names = params.split(',') if isinstance(params, str) else params; out = {}
        if len(n.b) > len(names): self.r.refuse(f'`{n.a}()` takes at most {len(names)} arguments here.', n.line)
        out.update(zip(names, n.b))
        for key, value in n.c.items():
            if key in names: out[key] = value
            elif not loose: self.r.refuse(f'`{n.a}()` has no `{key}` argument here.', n.line)
        return out

    def args(self, n, spec):
        """Closures for a built-in's arguments, in order. `spec` lists names; `name=value` gives a default."""
        names = []; defaults = {}
        for part in spec.split(','):
            name, _, default = part.partition('=')
            names.append(name)
            if default: defaults[name] = json.loads(default)
        given = self.bind(n, names); out = []; missing = False
        for name in names:
            if name in given: out.append(self.ex(given[name]))
            elif name in defaults: v = defaults[name]; out.append(lambda fr, v=v: v)
            else: missing = True; self.r.refuse(f'`{n.a}()` needs `{name}`.', n.line)
        return None if missing else out

    def call(self, n):
        name = n.a
        if not self.quiet and name.startswith('ta.') and (name in self.funcs or name in BUILTINS): self.r.uses.add(name)
        if name in self.funcs: return self.user_call(n)
        if name in BUILTINS: return BUILTINS[name](self, n)
        root = name.split('.')[0]
        if root in UNSUPPORTED_ROOT: return self.refuse(UNSUPPORTED_ROOT[root], n.line)
        if root in VISUAL_ROOTS or name in VISUAL_CALLS:
            if '.get_' in name: return self.refuse(f'`{name}()` reads a value back from a drawing. Drawings are not created here, so there is nothing to read.', n.line)
            self.r.ignore('visual', 'Plots, drawings, colours and alerts are skipped. They do not change trades.', n.line)
            return lambda fr: None
        if root in ('strategy', 'request'): return self.refuse(f'`{name}()` is not supported yet.', n.line)
        return self.refuse(f'`{name}()` is not a function this converter knows yet.', n.line)

    def user_call(self, n):
        layout, params, body = self.funcs[n.a]; given = self.bind(n, [p for p, _, _ in params]); pairs = []
        for p, slot, default in params:
            if p in given: pairs.append((slot, self.ex(given[p])))
            elif default is not None: pairs.append((slot, self.ex(default)))
            else: return self.refuse(f'`{n.a}()` needs `{p}`.', n.line)
        site = self.layout.state()
        def f(fr):
            inner = fr.s[site]
            if inner is None: inner = fr.s[site] = Frame(layout)
            v = inner.v
            for slot, a in pairs: v[slot] = a(fr)
            result = body(inner)
            for slot, h in inner.h.items(): h.append(v[slot])
            return result
        return f


def pure(fn, spec, names):
    def handler(cx, n):
        a = cx.args(n, spec)
        if a is None: return lambda fr: NAN
        if len(a) == 1: x, = a; return lambda fr: fn(x(fr))
        if len(a) == 2: x, y = a; return lambda fr: fn(x(fr), y(fr))
        return lambda fr: fn(*[x(fr) for x in a])
    for name in names.split(): BUILTINS[name] = handler


def variadic(fn, names):
    def handler(cx, n):
        a = [cx.ex(x) for x in n.b]
        if not a or n.c: return cx.refuse(f'`{n.a}()` needs plain values.', n.line)
        return lambda fr: fn(*[x(fr) for x in a])
    for name in names.split(): BUILTINS[name] = handler


def stateful(cls, spec, names, source=None, extra=()):
    """A built-in that remembers earlier bars. `source` is the series used when the script leaves the first argument out."""
    first = spec.split(',')[0].split('=')[0]; count = len(spec.split(','))
    def handler(cx, n):
        if source and first not in n.c and len(n.b) < count and not (len(n.b) + len(n.c) == count):
            n = Node('call', n.line, n.a, [Node('name', n.line, source), *n.b], n.c)
        a = cx.args(n, spec)
        if a is None: return lambda fr: NAN
        a = a + [cx.ex(Node('name', n.line, x) if isinstance(x, str) else x) for x in extra]; site = cx.layout.state()
        def f(fr):
            st = fr.s[site]
            if st is None: st = fr.s[site] = cls()
            return st.step(*[x(fr) for x in a])
        return f
    for name in names.split(): BUILTINS[name] = handler


pure(nz, 'source,replacement=0', 'nz')
pure(isna, 'x', 'na')
pure(lambda x: NAN if isna(x) else abs(x), 'number', 'math.abs')
pure(pround, 'number,precision=null', 'math.round')
pure(guard(math.floor), 'number', 'math.floor')
pure(guard(math.ceil), 'number', 'math.ceil')
pure(guard(math.sqrt), 'number', 'math.sqrt')
pure(guard(math.pow), 'base,exponent', 'math.pow')
pure(guard(math.exp), 'number', 'math.exp')
pure(guard(math.log), 'number', 'math.log')
pure(guard(math.log10), 'number', 'math.log10')
pure(guard(lambda x: (x > 0) - (x < 0)), 'number', 'math.sign')
pure(guard(math.sin), 'angle', 'math.sin'); pure(guard(math.cos), 'angle', 'math.cos'); pure(guard(math.tan), 'angle', 'math.tan')
pure(guard(math.asin), 'value', 'math.asin'); pure(guard(math.acos), 'value', 'math.acos'); pure(guard(math.atan), 'value', 'math.atan')
pure(guard(math.degrees), 'radians', 'math.todegrees'); pure(guard(math.radians), 'degrees', 'math.toradians')
pure(to_int, 'x', 'int'); pure(to_float, 'x', 'float'); pure(truth, 'x', 'bool'); pure(lambda x: x, 'x', 'string')
pure(to_string, 'value,format=null', 'str.tostring')
variadic(pmax, 'math.max'); variadic(pmin, 'math.min'); variadic(guard(pavg), 'math.avg'); variadic(str_format, 'str.format'); variadic(timestamp, 'timestamp')

stateful(Sma, 'source,length', 'ta.sma'); stateful(Ema, 'source,length', 'ta.ema'); stateful(Rma, 'source,length', 'ta.rma')
stateful(Wma, 'source,length', 'ta.wma'); stateful(Hma, 'source,length', 'ta.hma'); stateful(Swma, 'source', 'ta.swma')
stateful(Vwma, 'source,length', 'ta.vwma', extra=('volume',))
stateful(Alma, 'series,length,offset,sigma,floor=false', 'ta.alma'); stateful(Linreg, 'source,length,offset=0', 'ta.linreg')
stateful(Stdev, 'source,length,biased=true', 'ta.stdev'); stateful(Variance, 'source,length,biased=true', 'ta.variance'); stateful(Dev, 'source,length', 'ta.dev')
stateful(Highest, 'source,length', 'ta.highest', source='high'); stateful(Lowest, 'source,length', 'ta.lowest', source='low')
stateful(HighestBars, 'source,length', 'ta.highestbars', source='high'); stateful(LowestBars, 'source,length', 'ta.lowestbars', source='low')
stateful(Change, 'source,length=1', 'ta.change ta.mom'); stateful(Roc, 'source,length', 'ta.roc')
stateful(Crossover, 'source1,source2', 'ta.crossover'); stateful(Crossunder, 'source1,source2', 'ta.crossunder'); stateful(Cross, 'source1,source2', 'ta.cross')
stateful(Rsi, 'source,length', 'ta.rsi'); stateful(Stoch, 'source,high,low,length', 'ta.stoch'); stateful(Cci, 'source,length', 'ta.cci')
stateful(Bands, 'series,length,mult', 'ta.bb'); stateful(Macd, 'source,fastlen,slowlen,siglen', 'ta.macd')
stateful(Keltner, 'series,length,mult,useTrueRange=true', 'ta.kc', extra=('ta.tr', Node('bin', 0, '-', Node('name', 0, 'high'), Node('name', 0, 'low'))))
stateful(BarsSince, 'condition', 'ta.barssince'); stateful(ValueWhen, 'condition,source,occurrence=0', 'ta.valuewhen')
stateful(Rising, 'source,length', 'ta.rising'); stateful(Falling, 'source,length', 'ta.falling')
stateful(Cum, 'source', 'ta.cum'); stateful(RollSum, 'source,length', 'math.sum'); stateful(FixNan, 'source', 'fixnan')
stateful(Pivot, 'source,leftbars,rightbars', 'ta.pivothigh', source='high'); stateful(PivotLow, 'source,leftbars,rightbars', 'ta.pivotlow', source='low')


@builtin('ta.atr')
def _atr(cx, n):
    a = cx.args(n, 'length'); m = cx.m; site = cx.layout.state()
    if a is None: return lambda fr: NAN
    def f(fr):
        st = fr.s[site]
        if st is None: st = fr.s[site] = Atr()
        return st.step(m.TRH[m.i], a[0](fr))
    return f


@builtin('ta.tr')
def _tr(cx, n):
    a = cx.args(n, 'handle_na=false'); m = cx.m
    return lambda fr: (m.TRH if truth(a[0](fr)) else m.TR)[m.i]


@builtin('ta.vwap')
def _vwap(cx, n):
    if len(n.b) + len(n.c) > 1: return cx.refuse('ta.vwap with an anchor or bands is not supported; only the daily session VWAP is.', n.line)
    m = cx.m; given = cx.bind(n, 'source')
    if 'source' not in given or (given['source'].k == 'name' and given['source'].a == 'hlc3'): return lambda fr: m.VWAP[m.i]
    source = cx.ex(given['source']); site = cx.layout.state()
    def f(fr):
        # Session VWAP of an arbitrary source: the sums restart with each UTC day, as the built-in series does.
        st = fr.s[site]; day = m.DAYKEY[m.i]; v = m.V[m.i]
        if st is None or st[0] != day: st = fr.s[site] = [day, 0., 0.]
        st[1] += source(fr) * v; st[2] += v
        return div(st[1], st[2])
    return f


@builtin('timeframe.in_seconds')
def _in_seconds(cx, n):
    a = cx.args(n, 'timeframe=null'); m = cx.m
    return lambda fr: 60 * (m.tf if a[0](fr) in (None, '') else timeframe_minutes(a[0](fr)))


@builtin('timeframe.change')
def _tf_change(cx, n):
    a = cx.args(n, 'timeframe'); m = cx.m
    if a is None: return lambda fr: False
    def f(fr):
        span = timeframe_minutes(a[0](fr)) * 60000; i = m.i
        return i > 0 and m.T[i] // span != m.T[i - 1] // span
    return f


@builtin('request.security')
def _security(cx, n):
    given = cx.bind(n, 'symbol,timeframe,expression,gaps,lookahead,ignore_invalid_symbol,currency,calc_bars_count'); m = cx.m
    named = lambda key: given[key].a if key in given and given[key].k == 'name' else None
    if any(k not in given for k in ('symbol', 'timeframe', 'expression')): return cx.refuse('request.security needs a symbol, a timeframe and an expression.', n.line)
    if not (named('symbol') in ('syminfo.tickerid', 'syminfo.ticker') or literal(given['symbol']) == ''):
        return cx.refuse('request.security for another symbol is not supported: only the market being tested is loaded. Use syminfo.tickerid.', n.line)
    ahead = named('lookahead') == 'barmerge.lookahead_on'; expression = given['expression']; kept = expression
    if ahead:
        # `expr[1]` with lookahead_on is the usual way to ask for the last completed higher-timeframe candle from the
        # first chart candle of the next one. That is honest. Without the offset it would read an unfinished candle.
        kept = lagged(expression)
        if kept is None: return cx.refuse('request.security with lookahead_on and no [1] offset hands the script a higher-timeframe candle before it has closed. Offset the expression by [1], or remove the lookahead argument.', n.line)
    if named('gaps') == 'barmerge.gaps_on': return cx.refuse('request.security with gaps_on is not supported yet.', n.line)
    if 'lookahead' in given and named('lookahead') not in ('barmerge.lookahead_off', 'barmerge.lookahead_on') or 'gaps' in given and named('gaps') != 'barmerge.gaps_off':
        return cx.refuse('request.security needs its gaps and lookahead arguments written out as barmerge constants.', n.line)
    if isinstance(literal(given['timeframe']), str) and literal(given['timeframe']):
        try: timeframe_minutes(literal(given['timeframe']))
        except PineError as e: return cx.refuse(e.text, n.line)
    key = id(n)
    if cx.capture is not None:
        # Inside the higher-timeframe copy of the script the request is the expression itself; its value is kept for the chart.
        value = cx.ex(expression); out = cx.capture; keep = cx.ex(kept) if ahead else None
        def f(fr):
            v = value(fr); out[key] = keep(fr) if keep else v; return v
        return f
    if cx.inside or len(cx.scopes) > 1: return cx.refuse('request.security inside a function or a block is not supported yet. Call it at the top level of the script.', n.line)
    cx.r.approx('security', 'request.security: the script is also run on higher-timeframe candles built from the same market, and a value reaches the chart only when its candle has closed. That is what TradingView shows on history; on a live chart TradingView can show the unfinished candle, which a backtest must not use.', n.line)
    tf = cx.ex(given['timeframe'])
    def f(fr):
        minutes = tf(fr); return m.higher(m.tf if minutes in ('', None) else timeframe_minutes(minutes)).value(key, (m.T if ahead else m.TC)[m.i])
    return f


def lagged(node):
    """`x[k]` with k of at least 1, moved one candle earlier; None if the expression is not offset that way."""
    if node.k == 'tuple':
        items = [lagged(x) for x in node.a]
        return None if any(x is None for x in items) else Node('tuple', node.line, items)
    k = literal(node.b) if node.k == 'sub' else None
    if type(k) is not int or k < 1: return None
    return node.a if k == 1 else Node('sub', node.line, node.a, Node('const', node.line, k - 1))


@builtin('time', 'time_close')
def _time(cx, n):
    return cx.refuse(f'`{n.a}()` with a timeframe or session is not supported yet. The plain `{n.a}` variable is.', n.line)


@builtin('input', 'input.int', 'input.float', 'input.bool', 'input.string', 'input.source', 'input.time', 'input.timeframe', 'input.session',
         'input.symbol', 'input.color', 'input.price', 'input.text_area')
def _input(cx, n):
    given = cx.bind(n, 'defval,title', loose=True); node = given.get('defval'); title = literal(given.get('title'))
    if node is None: return cx.refuse(f'`{n.a}()` needs a default value.', n.line)
    cx.r.inputs.append({'line': n.line, 'kind': n.a.partition('.')[2] or 'input', 'title': title if isinstance(title, str) else None, 'default': shown(node)})
    return cx.ex(node)


@builtin('indicator', 'library', 'study')
def _not_strategy(cx, n):
    return cx.refuse('This is an indicator or library, not a strategy. Only a script that places orders with strategy.entry can be tested.', n.line)


@builtin('strategy')
def _declare(cx, n):
    given = cx.bind(n, 'title,shorttitle', loose=True); r = cx.r; r.declared = True; get = lambda key: literal(n.c.get(key))
    title = literal(given.get('title'))
    if isinstance(title, str): r.title = title
    if isinstance(get('pyramiding'), int) and get('pyramiding') > 1:
        r.refuse(f'pyramiding={get("pyramiding")}: the engine holds one position per market, so stacked entries cannot be tested.', n.line)
    if get('calc_on_order_fills') is True:
        r.refuse('calc_on_order_fills=true re-runs the script inside a candle after each fill. History has no ticks to do that with.', n.line)
    if get('process_orders_on_close') is True:
        r.approx('on_close', 'process_orders_on_close=true: TradingView would fill at the signal candle\'s close. FibStein fills at the next open, the first price that can actually be traded after the signal.', n.line)
    if get('calc_on_every_tick') is True:
        r.ignore('every_tick', 'calc_on_every_tick only changes a live chart. History is always evaluated on completed candles.', n.line)
    if any(k in n.c for k in ('default_qty_type', 'default_qty_value', 'initial_capital', 'commission_type', 'commission_value', 'slippage',
                              'margin_long', 'margin_short', 'currency', 'use_bar_magnifier', 'risk_free_rate')):
        r.ignore('account', 'Capital, position size, commission, slippage and margin on the strategy() line are replaced by Settings, which model them in more detail.', n.line)
    return lambda fr: None


def sizing_note(cx, given, n):
    if 'qty' in given: cx.r.ignore('qty', 'Order quantities (qty) are replaced by the Settings sizing rule.', n.line)


def condition(cx, given):
    return cx.ex(given['when']) if 'when' in given else None


def full_size(cx, given, n):
    pct = literal(given.get('qty_percent'))
    if 'qty' in given or ('qty_percent' in given and pct != 100):
        cx.r.refuse(f'`{n.a}()` closes part of a position. The engine closes a position whole, so partial exits cannot be tested yet.', n.line)


@builtin('strategy.entry')
def _entry(cx, n):
    given = cx.bind(n, ENTRY[cx.version]); m = cx.m; line = n.line; r = cx.r; r.orders += 1
    for key in ('limit', 'stop'):
        if key in given: r.refuse(f'strategy.entry with a {key} price is a resting order. Only market entries, filled at the next open, are supported yet.', line)
    if 'id' not in given or 'direction' not in given: return cx.refuse('strategy.entry needs an id and a direction.', line)
    sizing_note(cx, given, n); ident = cx.ex(given['id']); direction = cx.ex(given['direction']); when = condition(cx, given)
    if isinstance(literal(given['id']), str): r.entries[literal(given['id'])] = shown(given['direction'])
    def f(fr):
        if when is not None and not truth(when(fr)): return
        side = {'long': 1, 'short': -1}.get(direction(fr))
        if side is None: raise PineError('strategy.entry direction must be strategy.long or strategy.short.')
        m.commands.append(('entry', ident(fr), side))
    return f


@builtin('strategy.close')
def _close(cx, n):
    given = cx.bind(n, CLOSE[cx.version]); m = cx.m; cx.r.orders += 1
    if 'id' not in given: return cx.refuse('strategy.close needs the id of the entry to close.', n.line)
    full_size(cx, given, n); ident = cx.ex(given['id']); when = condition(cx, given)
    if literal(given.get('immediately')) is True:
        cx.r.approx('immediately', 'immediately=true: TradingView would close at the signal candle\'s close. FibStein closes at the next open.', n.line)
    cx.r.exits['close'] = 'strategy.close: leaves at the next open'
    def f(fr):
        if when is None or truth(when(fr)): m.commands.append(('close', ident(fr)))
    return f


@builtin('strategy.close_all')
def _close_all(cx, n):
    given = cx.bind(n, CLOSE_ALL[cx.version]); m = cx.m; when = condition(cx, given); cx.r.orders += 1
    cx.r.exits['close'] = 'strategy.close: leaves at the next open'
    def f(fr):
        if when is None or truth(when(fr)): m.commands.append(('close', None))
    return f


@builtin('strategy.exit')
def _exit(cx, n):
    given = cx.bind(n, EXIT); m = cx.m; r = cx.r; r.orders += 1
    if 'profit' in given or 'loss' in given:
        r.refuse('strategy.exit with profit= or loss= measures in ticks, which a strategy cannot see here. Pass a price with limit= or stop= instead.', n.line)
    if any(k in given for k in ('trail_price', 'trail_points', 'trail_offset')):
        r.refuse('strategy.exit with a built-in trailing stop (trail_*) is not supported yet. A stop= price that the script itself moves each bar is.', n.line)
    full_size(cx, given, n)
    if 'id' not in given: return cx.refuse('strategy.exit needs an id.', n.line)
    if 'stop' not in given and 'limit' not in given: return cx.refuse('strategy.exit needs a stop= or limit= price.', n.line)
    ident = cx.ex(given['id']); entry = cx.ex(given['from_entry']) if 'from_entry' in given else None; when = condition(cx, given)
    stop = cx.ex(given['stop']) if 'stop' in given else None; limit = cx.ex(given['limit']) if 'limit' in given else None
    if stop: r.exits['stop'] = 'strategy.exit stop: the script\'s own stop price'
    if limit: r.exits['limit'] = 'strategy.exit limit: the script\'s own target price'
    def f(fr):
        if when is None or truth(when(fr)):
            m.commands.append(('exit', ident(fr), entry(fr) if entry else None, stop(fr) if stop else NAN, limit(fr) if limit else NAN))
    return f


@builtin('strategy.order', 'strategy.cancel', 'strategy.cancel_all')
def _orders(cx, n):
    return cx.refuse(f'`{n.a}()` manages resting orders. Only market entries with strategy.entry are supported yet.', n.line)


@builtin('strategy.risk.allow_entry_in', 'strategy.risk.max_drawdown', 'strategy.risk.max_intraday_loss', 'strategy.risk.max_position_size',
         'strategy.risk.max_cons_loss_days', 'strategy.risk.max_intraday_filled_orders')
def _risk(cx, n):
    return cx.refuse(f'`{n.a}()` is not modelled. Use Settings for direction and risk limits.', n.line)


def trade_field(name, read):
    @builtin(f'strategy.opentrades.{name}')
    def handler(cx, n):
        a = cx.args(n, 'trade_num'); m = cx.m; _ = cx.name(Node('name', n.line, 'strategy.position_size'))
        return lambda fr: read(m) if m.pos and a and a[0](fr) == 0 else NAN

trade_field('entry_price', lambda m: m.avg); trade_field('entry_bar_index', lambda m: m.entry_bar)
trade_field('entry_time', lambda m: m.T[m.entry_bar]); trade_field('entry_id', lambda m: m.entry_id)


# ---------------------------------------------------------------- runtime

class Machine:
    """One run of one script over one market's candles, and the position TradingView would report to it.

    Given settings it places orders and builds the plan. Without them it is a read-only copy of the script on
    higher-timeframe candles, which is how request.security is answered.
    """
    def __init__(self, f, minutes, c=None, program=None):
        self.n = len(f); self.i = 0; self.line = None; self.tf = minutes; self.g = None; self.loops = 0; self.deadline = None
        self.program = program; self.frame = f; self.contexts = {}; self.trading = c is not None
        for name, col in (('O', f.open), ('H', f.high), ('L', f.low), ('C', f.close), ('V', f.volume), ('HL2', (f.high + f.low) / 2),
                          ('HLC3', (f.high + f.low + f.close) / 3), ('OHLC4', (f.open + f.high + f.low + f.close) / 4),
                          ('HLCC4', (f.high + f.low + 2 * f.close) / 4)): setattr(self, name, col.astype(float).tolist())
        prev = f.close.shift(); spread = f.high - f.low
        true_range = pd.concat([spread, (f.high - prev).abs(), (f.low - prev).abs()], axis=1).max(axis=1)
        self.TR = true_range.where(prev.notna()).tolist(); self.TRH = true_range.tolist()
        # The frame is indexed by the time each candle became available: its close. Pine's `time` is its open.
        closes = f.index.as_unit('ms').asi8; span = minutes * 60000; opened = f.index - pd.Timedelta(minutes=minutes)
        self.TC = closes.tolist(); self.T = (closes - span).tolist()
        for name, values in (('YEAR', opened.year), ('MONTH', opened.month), ('DAY', opened.day), ('HOUR', opened.hour), ('MINUTE', opened.minute),
                             ('SECOND', opened.second), ('WEEKDAY', (opened.dayofweek + 1) % 7 + 1), ('WEEK', opened.isocalendar().week)):
            setattr(self, name, [int(x) for x in values])
        day = opened.normalize(); self.DAYKEY = day.as_unit('ms').asi8.tolist(); typical = (f.high + f.low + f.close) / 3
        self.VWAP = ((typical * f.volume).groupby(day).cumsum() / f.volume.groupby(day).cumsum().replace(0, np.nan)).tolist()
        self.OBV = (np.sign(f.close.diff()).fillna(0) * f.volume).cumsum().tolist()
        self.commands = []; self.pos = 0; self.avg = NAN; self.entry_id = None; self.entry_bar = 0; self.closed_trades = 0
        if not self.trading: return
        self.ATR = f.atr.tolist()
        # Orders are placed only where the engine can act on them: features are warm and the test has begun.
        valid = f[['fast', 'slow', 'atr', 'rsi']].notna().all(axis=1) & (f.atr > 0)
        self.active = (valid.to_numpy() & (closes >= int(pd.Timestamp(c.start, tz='UTC').timestamp() * 1000))).tolist()
        self.allowed = {'both': (1, -1), 'long': (1,), 'short': (-1,)}[c.direction]; self.stop_atr = c.stop_atr
        self.penetration = c.limit_penetration_bps / 10000 if c.target_order == 'limit' else 0.
        self.brackets = {}; self.stop = NAN; self.target = None; self.pending = None; self.closed_in_bar = 0
        n = self.n; self.side = np.zeros(n, dtype=int); self.flags = {k: np.zeros(n, dtype=bool) for k in ('exit_long', 'exit_short', 'no_target', 'no_time_exit', 'unfiltered')}
        self.prices = {k: np.full(n, np.nan) for k in ('stop_price', 'target_price', 'amend_stop', 'amend_target')}
        self.stats = {'bars': n, 'long_entries': 0, 'short_entries': 0, 'signal_exits': 0, 'moved_levels': 0, 'settings_stops': 0}
        self.trades = []     # The script's own view of its trades: (side, entry bar, exit bar). The engine's trades are the result.

    def flatten(self):
        self.trades.append((self.pos, self.entry_bar, self.i))
        self.pos = 0; self.avg = NAN; self.entry_id = None; self.brackets = {}; self.target = None; self.closed_trades += 1

    def fill(self, i):
        """Orders decided on the previous candle fill at this open; then this candle may reach the stop or target."""
        self.closed_in_bar = 0; p = self.pending
        if p is not None:
            self.pending = None
            if self.pos: self.flatten()
            if p['side']:
                self.pos = p['side']; self.avg = self.O[i]; self.entry_id = p['id']; self.entry_bar = i
                self.brackets = p['brackets']; self.stop = p['stop']; self.target = p['target']
        d = self.pos
        if d:
            hi = self.H[i]; lo = self.L[i]; t = self.target; gone = lo <= self.stop if d == 1 else hi >= self.stop
            if not gone and t is not None: gone = hi >= t * (1 + self.penetration) if d == 1 else lo <= t * (1 - self.penetration)
            if gone: self.closed_in_bar = d; self.flatten()

    def levels(self, side, brackets):
        """Tightest valid stop and nearest valid target across the script's exit orders."""
        stops = [s for s, _ in brackets.values() if s == s and 0 < s < math.inf]; limits = [x for _, x in brackets.values() if x == x and 0 < x < math.inf]
        return ((max if side == 1 else min)(stops) if stops else None), ((min if side == 1 else max)(limits) if limits else None)

    def resolve(self, i):
        """Turn this candle's order commands into the plan row the engine acts on at the next open."""
        commands = self.commands; pos = self.pos; leave = False; want = 0; ident = None
        # The script's model saw its stop or target reached inside this candle. The engine reaches the same level on its
        # execution candles and is already flat, so this instruction normally does nothing. It exists for the rare exact
        # touch that tick rounding decides differently: without it the two would disagree about the position from here on.
        if self.closed_in_bar: self.flags['exit_long' if self.closed_in_bar == 1 else 'exit_short'][i] = True
        for cmd in commands:
            if cmd[0] == 'close' and pos and cmd[1] in (None, self.entry_id): leave = True
            # Same direction while in a position is ignored, as with pyramiding off. Before the test starts, so is every entry.
            elif cmd[0] == 'entry' and cmd[2] != pos and self.active[i]: want = cmd[2]; ident = cmd[1]
        if want and want != pos:
            if pos: self.flags['exit_long' if pos == 1 else 'exit_short'][i] = True; self.stats['signal_exits'] += 1
            if want not in self.allowed:
                # Direction is restricted in Settings: an opposite entry only closes, as strategy.risk.allow_entry_in does.
                if pos: self.pending = {'side': 0}
                return
            # An exit order issued for the position being left can carry the other side's prices. A level that is
            # already on the wrong side of the signal close cannot belong to this entry, so it is not attached to it.
            price = self.C[i]; right = lambda level, sign: level if level == level and sign * want * (price - level) > 0 else NAN
            brackets = {c[1]: (right(c[3], 1), right(c[4], -1)) for c in commands if c[0] == 'exit' and c[2] in (None, '', ident)}
            stop, target = self.levels(want, brackets)
            if stop is None: stop = price - want * self.ATR[i] * self.stop_atr; self.stats['settings_stops'] += 1
            if not stop > 0: return
            self.side[i] = want; self.flags['no_time_exit'][i] = self.flags['unfiltered'][i] = True
            self.prices['stop_price'][i] = stop
            if target is None: self.flags['no_target'][i] = True
            else: self.prices['target_price'][i] = target
            self.stats['long_entries' if want == 1 else 'short_entries'] += 1
            self.pending = {'side': want, 'id': ident, 'brackets': brackets, 'stop': stop, 'target': target}
        elif leave:
            self.flags['exit_long' if pos == 1 else 'exit_short'][i] = True; self.stats['signal_exits'] += 1; self.pending = {'side': 0}
        elif pos:
            for c in commands:
                if c[0] == 'exit' and c[2] in (None, '', self.entry_id): self.brackets[c[1]] = (c[3], c[4])
            stop, target = self.levels(pos, self.brackets)
            if stop is not None and stop != self.stop: self.stop = stop; self.prices['amend_stop'][i] = stop; self.stats['moved_levels'] += 1
            if target is not None and target != self.target: self.target = target; self.prices['amend_target'][i] = target; self.stats['moved_levels'] += 1

    def higher(self, minutes):
        if minutes not in self.contexts: self.contexts[minutes] = Higher(self, minutes)
        return self.contexts[minutes]

    def start(self, body, layout, watch=None):
        self.body = body; self.g = Frame(layout); self.history = list(self.g.h.items())
        self.watched = {name: [] for name in watch or {}}; self.watching = [(self.watched[name], slot) for name, slot in (watch or {}).items()]

    def step(self, i):
        """One completed candle: fills from the last decision, the script, then this candle's decision."""
        g = self.g; self.i = i; self.loops = 0
        if self.trading: self.fill(i)
        self.commands.clear()
        try:
            for statement in self.body: statement(g)
        except PineError as e:
            raise PineError(e.text, e.line or self.line) from None
        except (_Break, _Continue): raise PineError('break or continue outside a loop.', self.line) from None
        except (TypeError, ValueError, IndexError, KeyError, AttributeError, ArithmeticError, RecursionError) as e:
            raise PineError(f'could not be evaluated ({e}).', self.line) from None
        for slot, h in self.history: h.append(g.v[slot])
        for values, slot in self.watching: values.append(g.v[slot])
        if self.trading and (self.commands or self.pos or self.closed_in_bar): self.resolve(i)

    def run(self, body, layout, watch=None):
        self.start(body, layout, watch)
        for i in range(self.n):
            if self.deadline and not i % 64 and time.monotonic() > self.deadline: raise PineError(f'the script took more than {CHECK_SECONDS} seconds on {self.n:,} candles.')
            self.step(i)

    def plan(self, index):
        return pd.DataFrame({'side': self.side, **self.flags, **self.prices}, index=index)


class Higher:
    """The same script on completed higher-timeframe candles of the same market.

    The candles are built from the chart's own, and one is used only once every chart candle inside it exists and
    the last of them has closed: never filled, never early (AGENTS.md, invariants 1 and 4).
    """
    def __init__(self, chart, minutes):
        if minutes < chart.tf or minutes % chart.tf or 1440 % minutes:
            raise PineError(f'request.security timeframe must be the chart timeframe ({chart.tf} minutes) or a whole multiple of it that divides a day; {minutes} minutes is not.')
        f = chart.frame; span = minutes * 60000; bucket = np.asarray(chart.T) // span
        g = f[['open', 'high', 'low', 'close', 'volume']].groupby(bucket)
        bars = g.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})[(g.size() == minutes // chart.tf).to_numpy()]
        bars.index = pd.to_datetime((bars.index.to_numpy() + 1) * span, unit='ms', utc=True)
        self.closes = bars.index.as_unit('ms').asi8.tolist(); self.out = {}; self.before = {}; self.last = -1; self.done = 0; program = chart.program
        self.m = Machine(bars, minutes, program=program); cx = Compiler(self.m, program.version, Report(), capture=self.out)
        self.m.start(cx.body(program.tree), cx.layout)

    def value(self, key, now):
        """The expression's value on the last higher-timeframe candle that closed at or before `now`."""
        closes = self.closes
        while self.done < len(closes) and closes[self.done] <= now:
            self.before = dict(self.out); self.last = closes[self.done]; self.m.step(self.done); self.done += 1
        # A reader that looks from the chart candle's open must not see a candle that closed after it.
        return (self.out if self.last <= now else self.before).get(key, NAN)


class Program:
    """A parsed script. Running it is deterministic and reads nothing but the candles and settings it is given."""
    def __init__(self, source):
        self.source = source; self.tokens, self.version = lex(source); self.digest = digest(self.tokens); self.tree = None
        self.report = Report(); self.report.version = self.version; r = self.report
        if self.version is None: r.refuse('The script has no //@version= line. Pine v5 or v6 is needed.')
        elif self.version < 5: r.refuse(f'This is Pine v{self.version}. Open it in TradingView\'s Pine Editor, choose "Convert code to v6" (or v5), and paste the result.')
        elif self.version > 6: r.refuse(f'Pine v{self.version} is newer than this converter. v5 and v6 are supported.')
        for kind, value, line in self.tokens:
            if kind == 'kw' and value in UNSUPPORTED_KW: r.refuse(UNSUPPORTED_KW[value], line)
            elif kind == 'id' and value.split('.')[0] in UNSUPPORTED_ROOT: r.refuse(UNSUPPORTED_ROOT[value.split('.')[0]], line)
        if r.refused: return
        try: self.tree = Parser(self.tokens).program()
        except PineError as e: r.refuse(f'This line could not be read as Pine v{self.version}: {e.text}', e.line)

    def execute(self, f, c, report=None, watch=(), seconds=None):
        """Run over a feature frame. Returns the Machine, which holds the plan and what happened.

        `watch` names script variables whose value on every candle is kept in `machine.watched`, for inspection and tests.
        """
        m = Machine(f, c.timeframe, c, self); cx = Compiler(m, self.version, report or Report()); body = cx.body(self.tree)
        if seconds: m.deadline = time.monotonic() + seconds
        missing = [name for name in watch if name not in cx.top]
        if missing: raise PineError(f'no script variable named {missing[0]}.')
        if report is None or not report.refused: m.run(body, cx.layout, {name: cx.top[name] for name in watch})
        return m


def check(source, dry_run=True):
    """The conversion report for a script. Nothing is saved and nothing is registered."""
    try: program = Program(source)
    except PineError as e:
        r = Report(); r.refuse(e.text, e.line); return r.out()
    r = program.report
    if r.refused: return r.out()
    c = Config(strategy='trend_pullback', timeframe=15, start='2025-01-01', end='2025-01-22', htf_filter='off')
    try: m = program.execute(features(_synthetic(), c), c, r, seconds=CHECK_SECONDS)
    except PineError as e: r.refuse(e.text, e.line); return r.out()
    if not r.refused and not r.declared: r.refuse('The script has no strategy() line. Only a strategy, not an indicator, can be tested.')
    if not r.refused and not _has_entry(program.tree): r.refuse('The script never calls strategy.entry, so there is nothing to test.')
    if r.refused: return r.out()
    if m.stats['settings_stops']:
        r.approx('settings_stop', 'Some entries have no stop price from the script at the moment they are placed. The engine cannot size or liquidate a position without one, so those entries start with the Settings ATR stop, measured from the signal candle\'s close. If the script sets its stop a bar later, that stop then replaces it.')
    r.approx('fills', 'Entries and signal exits fill at the next open, as in TradingView\'s default. The script\'s own costs and order sizes are not used: fees, spread, slippage, funding, sizing and liquidation come from Settings.')
    extra = {'digest': program.digest, 'dry_run': {**m.stats, 'candles': '15-minute synthetic random walk'}}
    if dry_run:
        from .causality import prefix_invariant
        key = f'pine_check_{uuid.uuid4().hex[:12]}'; REGISTRY[key] = strategy_function(program)
        try: out = prefix_invariant(key, Config(strategy=key, timeframe=15, start='2025-01-01', end='2025-01-22', htf_filter='off'), [_synthetic()], cuts=8)
        finally: REGISTRY.pop(key, None)
        extra['look_ahead'] = {'ok': out['ok'], 'conclusive': out['conclusive'], 'signals_checked': out['signals_checked']}
        if not out['ok']: r.refuse('The converted script changed past signals when later candles were added. That is a converter fault; the script was not added.')
    return r.out(**extra)


def _has_entry(tree):
    def walk(x):
        if isinstance(x, Node): return (x.k == 'call' and x.a == 'strategy.entry') or any(walk(y) for y in (x.a, x.b, x.c, x.d))
        if isinstance(x, (list, tuple)): return any(walk(y) for y in x)
        if isinstance(x, dict): return any(walk(y) for y in x.values())
        return False
    return walk(tree)


_SYNTHETIC = []


def _synthetic():
    """Three weeks of random-walk minutes. Enough to exercise a script; never evidence of anything."""
    if not _SYNTHETIC:
        from .causality import synthetic
        _SYNTHETIC.append(synthetic(n=30000, seed=19))
    return _SYNTHETIC[0]


def strategy_function(program):
    """The function the engine calls: feature frame and settings in, plan frame out."""
    cache = OrderedDict()
    def run(f, c):
        if c.entry_order != 'market' or c.trailing_atr or c.breakeven_r:
            raise ValueError('A Pine strategy manages its own entries and stops. Set Entry order to Market, and Trailing distance and Breakeven trigger to 0.')
        # Everything the plan can depend on. A grid over settings the script never reads reuses the plan.
        key = (c.timeframe, c.direction, c.stop_atr, c.target_order, c.limit_penetration_bps, str(c.start), len(f), f.index[0], f.index[-1],
               float(f.close.sum()), float(f.volume.sum()), float(np.nansum(f.atr.to_numpy())))
        if key not in cache:
            cache[key] = program.execute(f, c).plan(f.index)
            while len(cache) > CACHE_SIZE: cache.popitem(last=False)
        cache.move_to_end(key); return cache[key]
    return run


# ---------------------------------------------------------------- saved scripts

RULE = 'Pine Script v{version}, run candle by candle. Entries, exits, stops and targets come from the script; costs, sizing, funding and liquidation come from Settings.'


def slug(title):
    s = re.sub(r'[^a-z0-9]+', '_', (title or 'script').lower()).strip('_')[:24].strip('_')
    return s if s and s[0].isalpha() else f's_{s}'.strip('_')[:24]


def key_of(program, name=None): return f'pine_{slug(name or program.report.title)}_{program.digest[:6]}'


def register(program, key, name):
    if key not in REGISTRY: register_strategy(key, name, RULE.format(version=program.version), strategy_function(program))


def add(source, folder, name=None, parent=None):
    """Check, save and register a script. The key includes a hash of its logic, so an edited script is a new strategy."""
    report = check(source)
    if not report['ok']:
        first = report['refused'][0]
        raise ValueError('This script cannot be tested yet. ' + (f'Line {first["line"]}: ' if first['line'] else '') + first['text'])
    program = Program(source); folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    existing = next((x for x in listing(folder) if x['digest'] == program.digest), None)
    if existing: register(program, existing['key'], existing['name']); return {**existing, 'report': report, 'existing': True}
    name = (name or report['title'] or 'Pine strategy').strip()[:80]; key = key_of(program, name)
    meta = {'key': key, 'name': name, 'version': program.version, 'digest': program.digest, 'parent': parent, 'created': datetime.now(timezone.utc).isoformat()}
    (folder/f'{key}.pine').write_text(source, encoding='utf-8'); (folder/f'{key}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    register(program, key, name); return {**meta, 'report': report, 'existing': False}


def listing(folder):
    out = []
    for path in sorted(Path(folder).glob('pine_*.json')):
        try: out.append(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError): continue
    return sorted(out, key=lambda x: x.get('created', ''), reverse=True)


def read(folder, key):
    if not re.fullmatch(r'pine_[a-z0-9_]{1,34}', key): raise ValueError('Not a Pine strategy key.')
    path = Path(folder)/f'{key}.pine'
    if not path.exists(): raise ValueError(f'No saved script for {key}.')
    meta = next((x for x in listing(folder) if x['key'] == key), {'key': key})
    return {**meta, 'source': path.read_text(encoding='utf-8')}


def load_saved(folder):
    """Register every saved script at start-up. A file whose logic no longer matches its key is skipped, not trusted."""
    loaded = []; skipped = []
    for meta in listing(folder):
        try:
            program = Program((Path(folder)/f'{meta["key"]}.pine').read_text(encoding='utf-8'))
            if program.digest != meta['digest'] or program.report.refused: raise ValueError('changed')
            register(program, meta['key'], meta['name']); loaded.append(meta['key'])
        except (OSError, ValueError, KeyError): skipped.append(meta.get('key'))
    return {'loaded': loaded, 'skipped': skipped}
