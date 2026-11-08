from tobili.domain.result import official_decision
from tobili.domain.tidy import Outcome


def notice():
    return {
        "status": 2,
        "first_prize": 1,
        "second_prize": 0,
        "third_prize": 0,
        "lottery_result": {"first_prize_result": [{"uid": "123"}]},
    }


def test_complete_official_uid_list():
    assert official_decision(notice(), "123").outcome is Outcome.WON
    assert official_decision(notice(), "456").outcome is Outcome.LOST


def test_incomplete_unknown_and_duplicate_rosters_never_mean_lost():
    from copy import deepcopy

    variations = []
    n = notice()
    n["first_prize"] = 2
    variations.append(n)
    n = notice()
    n["status"] = 0
    variations.append(n)
    n = notice()
    n["lottery_result"]["extra"] = []
    variations.append(n)
    n = notice()
    n["first_prize"] = True
    variations.append(n)
    n = notice()
    n["lottery_result"]["first_prize_result"] = [{"name": "nobody"}]
    variations.append(n)
    n = notice()
    n["second_prize"] = 1
    n["lottery_result"]["second_prize_result"] = [{"uid": "123"}]
    variations.append(n)
    n = notice()
    n["lottery_result"] = {}
    variations.append(n)
    for n in variations:
        assert official_decision(deepcopy(n), "456").outcome is Outcome.PENDING
