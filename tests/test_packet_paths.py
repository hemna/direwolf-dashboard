"""Raw TNC2 packet-path contract from the shared basemap-kit#63 spec (#205).

Ported from haminfo@8dabb959 tests/test_packet_paths.py: the haminfo-only
websocket-polling test is not ported (that pipeline is wired in Task 2 here).
Fixtures retain unknown calls; resolving or skipping positions is client work.
"""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

CASES = json.loads((Path(__file__).parent / "fixtures/packet_paths.json").read_text())
FIELDS = {
    "packet_id",
    "source",
    "hops",
    "rx_igate",
    "tx_igate",
    "no_rf_leg",
    "tx_hops",
}


def parse(raw):
    # Missing production parser is a failing assertion, not a collection error.
    spec = importlib.util.find_spec("direwolf_dashboard.packet_paths")
    assert spec is not None, "server-side packet path parser is not implemented"
    return importlib.import_module("direwolf_dashboard.packet_paths").parse_packet_path(raw)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_shared_fixtures(case):
    result = parse(case["raw"])
    assert result is not None
    assert set(result) == FIELDS
    for name, value in case["expected"].items():
        assert result[name] == value
    if "canonical" in case:
        assert result["packet_id"] == hashlib.sha1(case["canonical"].encode()).hexdigest()


def test_crlf_independent_identity():
    raw = "N0CALL>APRS,K4DIG*,WIDE2-1,qAR,K4ABC:hello:world"
    result = parse(raw + "\r\n")
    assert result["packet_id"] == hashlib.sha1(b"N0CALL>APRS:hello:world").hexdigest()
    assert result["packet_id"] == parse(raw)["packet_id"]


def test_multiple_paths_share_key_but_different_info_does_not():
    first = parse("N0CALL>APRS,K4DIG*,qAR,K4ABC:hello")
    second = parse("N0CALL>APRS,qAR,K4XYZ:hello")
    assert first["packet_id"] == second["packet_id"]
    assert first["hops"] != second["hops"]
    assert first["packet_id"] != parse("N0CALL>APRS,qAR,K4XYZ:hello!")["packet_id"]
    assert first["packet_id"] != parse("N0CALL>APXYZ,qAR,K4XYZ:hello")["packet_id"]


def test_message_key_ignores_header_destination_but_not_addressee_or_id():
    first = parse("N0CALL>APRS::K5DEST   :hello{42")
    assert first["packet_id"] == parse("N0CALL>APXYZ::K5DEST   :hello{42")["packet_id"]
    assert first["packet_id"] != parse("N0CALL>APRS::K4DEST   :hello{42")["packet_id"]
    assert first["packet_id"] != parse("N0CALL>APRS::K5DEST   :hello{43")["packet_id"]


def test_rx_tx_and_nested_position_correlation():
    by_id = {case["id"]: case["raw"] for case in CASES}
    assert parse(by_id["rx_correlated"])["packet_id"] == parse(by_id["tx_correlated"])["packet_id"]
    rx = "N0CALL>APRS,K4RX*:!3740.00N/..."
    tx = "K4TX>APRS,K4DIG*:}K4MID>APRS:}N0CALL>APRS,TCPIP,K4TX*:!3740.00N/..."
    assert parse(rx)["packet_id"] == parse(tx)["packet_id"]


@pytest.mark.parametrize("construct", ["qAC", "qAX", "qAU"])
def test_internet_only_outer_frames_do_not_emit_tx_rf_hops(construct):
    result = parse(f"K4TX>APRS,{construct},SERVER:}}N0CALL>APRS,TCPIP,K4TX*::K5DEST   :hello{{42")
    assert result["no_rf_leg"] is True
    assert result["tx_hops"] == []


@pytest.mark.parametrize(
    "raw",
    [
        None,
        123,
        b"N0CALL>APRS:...",
        "",
        "not a packet",
        "N0CALL>APRS",
        ">APRS:...",
        "N0CALL>:...",
        "N0CALL>APRS:",
        "N0CALL>APRS,:...",
        "N0CALL>APRS,K4DIG**:...",
        "N0CALL>APRS,<script>*:...",
        "N0CALL>APRS,K4DIG-99*:...",
        "N0CALL>APRS,qAR:...",
        "N0CALL>APRS,qAR,<script>:...",
        "N0CALL>APRS:}\n",
        "N0CALL>APRS,qAR*,K4ABC:...",
        "N0CALL>APRS:bad\ud800",
        "K4TX>APRS:}broken",
        "N0CALL>APRS:hello\nother",
        "N0CALL>APRS:hello\x00",
        "K4TX>APRS:}" * 5 + "N0CALL>APRS:...",
        "N0CALL>APRS," + ",".join(["K4DIG*"] * 65) + ":...",
        "N0CALL>APRS:" + "x" * 8192,
        # Bad addressee: info[10] == ':' but the addressee fails its regex.
        "N0CALL>APRS::-K5DEST  :hello",
    ],
    ids=lambda raw: repr(raw)[:65],
)
def test_malformed_or_unbounded_raw_has_no_identity(raw):
    assert parse(raw) is None
