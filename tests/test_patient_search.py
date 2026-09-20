#   python tests/test_patient_search.py
import sys

from _pf_fixtures import make_repo, run_all


def ids(rows):
    return sorted(r["patient_id"] for r in rows)


def test_search_by_patient_id_in_any_format():
    r = make_repo()
    for q in ("VX-000421", "vx 421", "V X 4 2 1"):
        assert ids(r.search_patients(q)) == ["VX-000421"], q


def test_search_by_name_case_insensitive_substring():
    assert ids(make_repo().search_patients("ravi")) == ["VX-000422"]
    assert ids(make_repo().search_patients("DEMO")) == ["VX-000421"]


def test_search_by_phone_is_normalised():
    r = make_repo()
    assert ids(r.search_patients("+91 99900 01111")) == ["VX-000421"]
    assert ids(r.search_patients("9990001111")) == ["VX-000421"]


def test_short_or_empty_query_returns_nothing_and_never_lists_everyone():
    r = make_repo()
    for q in ("", " ", "a", None):
        assert r.search_patients(q) == []


def test_unknown_returns_empty():
    assert make_repo().search_patients("zzzz nobody") == []


def test_result_limit_is_respected():
    r = make_repo()
    for i in range(30):
        r.patients.append({"id": f"x{i}", "patient_id": f"VX-{i + 100:06d}", "full_name": f"Sam {i}", "phone": ""})
    assert len(r.search_patients("sam", limit=8)) == 8


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
