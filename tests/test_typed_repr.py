"""Tests for the OP.GG typed-repr parser."""

from __future__ import annotations

import pytest

from lolcoach.dataprovider.typed_repr import TypedReprError, parse_typed
from lolcoach.values import as_dict, as_float, as_list, as_str

_LANE_META = """class LolListLaneMetaChampions: data
class Data: positions
class Positions: mid
class Mid: champion,play,win_rate,pick_rate,ban_rate,tier,rank

LolListLaneMetaChampions(Data(Positions([Mid("Ahri",115676,0.51,0.09,0.03,1,1),Mid("Yone",107086,0.5,0.09,0.15,1,2)])))
"""

_NESTED = """class LolGetChampionAnalysis: champion,position,data
class Data: summary,strong_counters,synergies
class Summary: average_stats
class AverageStats: play,win_rate
class StrongCounter: champion_name,my_win_rate
class Synergies: mid
class Synergy: synergy_champion_name,win_rate

LolGetChampionAnalysis("GALIO","MID",Data(Summary(AverageStats(63923,0.49)),[StrongCounter("Azir",0.57)],Synergies([Synergy("Lucian",0.53)])))
"""


def test_parses_column_ordered_rows() -> None:
    root = as_dict(parse_typed(_LANE_META))
    rows = as_list(as_dict(as_dict(root["data"])["positions"])["mid"])
    first = as_dict(rows[0])
    assert as_str(first["champion"]) == "Ahri"
    assert first["play"] == 115676
    assert as_float(first["win_rate"]) == 0.51
    assert as_str(as_dict(rows[1])["champion"]) == "Yone"


def test_parses_nested_objects_and_lists() -> None:
    data = as_dict(as_dict(parse_typed(_NESTED))["data"])
    average = as_dict(as_dict(data["summary"])["average_stats"])
    assert average["play"] == 63923
    assert as_float(average["win_rate"]) == 0.49
    counters = as_list(data["strong_counters"])
    assert as_str(as_dict(counters[0])["champion_name"]) == "Azir"
    synergies = as_list(as_dict(data["synergies"])["mid"])
    assert as_str(as_dict(synergies[0])["synergy_champion_name"]) == "Lucian"


def test_booleans_and_apostrophes() -> None:
    root = as_dict(parse_typed('class R: flag,name\n\nR(false,"Kai\'Sa")\n'))
    assert root["flag"] is False
    assert as_str(root["name"]) == "Kai'Sa"


def test_arity_mismatch_raises() -> None:
    with pytest.raises(TypedReprError):
        parse_typed("class R: a,b\n\nR(1,2,3)\n")


def test_unknown_class_raises() -> None:
    with pytest.raises(TypedReprError):
        parse_typed("class R: a\n\nQ(1)\n")


def test_missing_headers_raises() -> None:
    with pytest.raises(TypedReprError):
        parse_typed("R(1,2)\n")
