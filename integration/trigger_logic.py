# SPDX-License-Identifier: LGPL-3.0-or-later
"""Parser and evaluation of the sandbox trigger and veto expressions."""
import operator
import re

_TOKEN = re.compile(r'\s*(>=|<=|==|[&|!()<>]|trg[0-3]|dt_trg|dt_veto|ntrg|bsy|[0-9]+)')
def clock_cycle(time_ps):
    return time_ps * 201 // 2000000


_COMPARE = {'>': operator.gt, '<': operator.lt, '>=': operator.ge,
            '<=': operator.le, '==': operator.eq}


class Expression:
    def __init__(self, tree, channels):
        self.tree, self.channels = tree, channels

    def evaluate(self, fired, dt_trg, dt_veto, bsy):
        variables = {'dt_trg': dt_trg, 'dt_veto': dt_veto, 'ntrg': len(fired), 'bsy': bsy}
        def calc(node):
            op = node[0]
            if op == 'trg': return node[1] in fired
            if op == 'bsy': return bsy
            if op == '!': return not calc(node[1])
            if op == '&': return calc(node[1]) and calc(node[2])
            if op == '|': return calc(node[1]) or calc(node[2])
            if op == 'false': return False
            return _COMPARE[op](variables[node[1]], node[2])
        return bool(calc(self.tree))


def parse(text, allow_empty=False):
    if not isinstance(text, str): raise ValueError('expression must be text')
    if not text.strip():
        if allow_empty: return Expression(('false',), set())
        raise ValueError('expression cannot be empty')
    tokens = []
    position = 0
    while position < len(text):
        if not text[position:].strip(): break
        match = _TOKEN.match(text, position)
        if not match: raise ValueError('unexpected character')
        tokens.append(match.group(1)); position = match.end()
    channels = set()
    position = 0
    def unary():
        nonlocal position
        if position >= len(tokens): raise ValueError('missing operand')
        token = tokens[position]; position += 1
        if token == '!': return ('!', unary())
        if token == '(':
            result = expression()
            if position >= len(tokens) or tokens[position] != ')': raise ValueError('missing closing parenthesis')
            position += 1
            return result
        if token.startswith('trg'):
            number = int(token[3:]); channels.add(number); return ('trg', number)
        if token == 'bsy': return ('bsy',)
        if token in ('dt_trg', 'dt_veto', 'ntrg'):
            if position + 1 >= len(tokens) or tokens[position] not in _COMPARE or not tokens[position+1].isdigit():
                raise ValueError('expected comparison and integer')
            op, number = tokens[position], int(tokens[position+1]); position += 2
            if number > 1_000_000_000_000: raise ValueError('integer must be from 0 to 1000000000000')
            return (op, token, number)
        raise ValueError('expected operand')
    def conjunction():
        nonlocal position
        result = unary()
        while position < len(tokens) and tokens[position] == '&':
            position += 1; result = ('&', result, unary())
        return result
    def expression():
        nonlocal position
        result = conjunction()
        while position < len(tokens) and tokens[position] == '|':
            position += 1; result = ('|', result, conjunction())
        return result
    tree = expression()
    if position != len(tokens): raise ValueError('unexpected token')
    return Expression(tree, channels)
