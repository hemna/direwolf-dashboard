"""packet_to_dict must carry the shared packet-path fields (#205, task 2).

Task 1 (tests/test_packet_paths.py) proves the parser itself; this file
wires it into the packet dict the map client consumes.  Fixtures come from
the shared tests/fixtures/packet_paths.json, same ids and expectations.
"""

import json
from pathlib import Path

from direwolf_dashboard.packet_paths import parse_packet_path
from direwolf_dashboard.processor import packet_to_dict

CASES = {
    case["id"]: case
    for case in json.loads(
        (Path(__file__).parent / "fixtures/packet_paths.json").read_text()
    )
}
FIELDS = {"packet_id", "source", "hops", "rx_igate", "tx_igate", "no_rf_leg", "tx_hops"}


def test_packet_to_dict_carries_plain_rf_fixture_fields():
    """A plain-rf fixture gains every field, matching a direct parse_packet_path."""
    case = CASES["used_unused"]
    result = packet_to_dict(case["raw"], tx=False, call_from="N0CALL", call_to="APRS")

    assert result is not None
    assert FIELDS <= set(result)
    direct = parse_packet_path(case["raw"])
    for name in FIELDS:
        assert result[name] == direct[name]
    # The existing line-label path key stays untouched (log panel + GPX read it).
    assert isinstance(result["path"], list)


def test_packet_to_dict_carries_third_party_fields():
    """Full TNC2 third-party frame: outer header survives to the parser."""
    case = CASES["third_party_tx"]
    result = packet_to_dict(case["raw"], tx=False, call_from="K4TX", call_to="APRS")

    assert result is not None
    assert FIELDS <= set(result)
    direct = parse_packet_path(case["raw"])
    for name in FIELDS:
        assert result[name] == direct[name]
    assert result["source"] == "N0CALL"
    assert result["hops"] == ["N0CALL"]
    assert result["tx_igate"] == "K4TX"
    assert result["tx_hops"] == ["K4TX"]
    assert result["no_rf_leg"] is False


def test_agw_third_party_payload_form_gets_same_fields():
    """AGW header + '}inner' payload: the reconstructed frame parses identically."""
    case = CASES["third_party_tx"]
    inner = case["raw"].split(":", 1)[1]  # }N0CALL>APRS,TCPIP,K4TX*:...
    raw = (
        "1:Fm K4TX To APRS Via WIDE1-1 <UI pid=F0 Len=64>[12:00:00]\r"
        f"{inner}"
    )
    result = packet_to_dict(raw, tx=False, call_from="K4TX", call_to="APRS")

    assert result is not None
    assert FIELDS <= set(result)
    direct = parse_packet_path(case["raw"])
    for name in FIELDS:
        assert result[name] == direct[name]
    assert result["via"] == "K4TX"  # existing third-party marker untouched


def test_malformed_packet_info_gets_empty_defaults():
    """Embedded-newline raw is rejected by the parser; dict keys still default."""
    result = packet_to_dict(
        "N0CALL>APRS:hello\nother", tx=False, call_from="N0CALL", call_to="APRS"
    )

    assert result is not None
    assert FIELDS <= set(result)
    assert result["packet_id"] is None
    assert result["source"] is None
    assert result["hops"] == []
    assert result["tx_hops"] == []
    assert result["rx_igate"] is None
    assert result["tx_igate"] is None
    assert result["no_rf_leg"] is False
