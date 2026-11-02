"""Bounded raw TNC2 path metadata for packet animations (no position guesses)."""

from __future__ import annotations

import hashlib
import re

MAX_RAW_LENGTH = 8192
MAX_PATH_TOKENS = 64
MAX_UNWRAP_DEPTH = 4

_CALL = re.compile(r'[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?\Z')
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,31}\*?\Z')
_Q = re.compile(r'qA[A-Za-z]\Z')
_ALIAS = re.compile(r'(?:WIDE|TRACE)(?:[0-9]+(?:-[0-9]+)?)?\Z')
_ALIASES = {'RELAY', 'ECHO', 'GATE', 'RFONLY', 'NOGATE', 'TCPIP', 'TCPXX', 'ARISS'}


def _is_alias(call: str) -> bool:
    return call in _ALIASES or bool(_ALIAS.fullmatch(call))


def _parse_frame(raw: str) -> tuple | None:
    header, sep, info = raw.partition(':')
    source, arrow, rest = header.partition('>')
    destination, comma, path = rest.partition(',')
    if not sep or not info or not arrow or not _CALL.fullmatch(source) or not _CALL.fullmatch(destination):
        return None
    tokens = path.split(',') if comma else []
    if len(tokens) > MAX_PATH_TOKENS or any(not _TOKEN.fullmatch(token) for token in tokens):
        return None
    trace = False
    for token in tokens:
        call = token.removesuffix('*')
        if _Q.fullmatch(call) and token.endswith('*'):
            return None
        trace = trace or bool(_Q.fullmatch(call))
        if not trace and not (_CALL.fullmatch(call) or _is_alias(call)):
            return None
    return source, destination, path, info, tokens


def _route(tokens: list[str]) -> tuple[list[str], str | None, str | None, bool]:
    q_index = next((i for i, token in enumerate(tokens) if _Q.fullmatch(token)), len(tokens))
    q_construct = tokens[q_index] if q_index < len(tokens) else None
    rf = tokens[:q_index]
    no_rf_leg = q_construct in {'qAC', 'qAX', 'qAU'} or any(
        token.removesuffix('*') == 'TCPIP' for token in rf
    )
    rx_igate = None
    if not no_rf_leg and q_construct in {'qAR', 'qAO', 'qAS'} and q_index + 1 < len(tokens):
        call = tokens[q_index + 1].removesuffix('*')
        if _CALL.fullmatch(call) and not _is_alias(call):
            rx_igate = call
    last_star = max((i for i, token in enumerate(rf) if token.endswith('*')), default=-1)
    used = [] if no_rf_leg else [
        token.removesuffix('*') for token in rf[:last_star + 1]
        if not _is_alias(token.removesuffix('*'))
    ]
    return used, rx_igate, q_construct, no_rf_leg


def _collapse(calls: list[str]) -> list[str]:
    return [call for i, call in enumerate(calls) if i == 0 or call != calls[i - 1]]


def parse_packet_path(raw: str) -> dict | None:
    """Parse raw (never the truncated DB path); return None for malformed frames.

    Identity is SHA1 of the innermost ``src>dest:info`` without any path.
    Messages substitute the addressee for dest; info retains the message ID.
    A third-party observation describes the outer TX RF leg separately from
    the original source. Unknown positions and recipient endpoints stay client-side.
    """
    if not isinstance(raw, str) or len(raw) > MAX_RAW_LENGTH:
        return None
    raw = raw.rstrip('\r\n')
    if any(char in raw for char in ('\r', '\n', '\x00')):
        return None
    outer = _parse_frame(raw)
    if outer is None:
        return None
    frame = outer
    depth = 0
    while frame[3].startswith('}'):
        if depth == MAX_UNWRAP_DEPTH:
            return None
        frame = _parse_frame(frame[3][1:])
        if frame is None:
            return None
        depth += 1

    source, destination, _, info, _ = frame
    used, rx_igate, q_construct, no_rf_leg = _route(outer[4])
    if q_construct in {'qAR', 'qAO', 'qAS'} and not no_rf_leg and rx_igate is None:
        return None
    addressee = None
    if info.startswith(':'):
        if len(info) < 11 or info[10] != ':':
            return None
        addressee = info[1:10].strip()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{0,8}', addressee):
            return None
    canonical = f'{source}>{addressee or destination}:{info}'
    try:
        packet_id = hashlib.sha1(canonical.encode('utf-8')).hexdigest()
    except UnicodeEncodeError:
        return None
    third_party = depth > 0
    return {
        'packet_id': packet_id,
        'source': source,
        'hops': [source] if third_party else _collapse([source] + used + ([rx_igate] if rx_igate else [])),
        'rx_igate': rx_igate,
        'tx_igate': outer[0] if third_party else None,
        'no_rf_leg': no_rf_leg,
        'tx_hops': _collapse([outer[0]] + used) if third_party and not no_rf_leg else [],
        'addressee': addressee,
    }
