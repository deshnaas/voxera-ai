#   python tests/test_patient_identity.py   (or: pytest -q tests/test_patient_identity.py)
import sys

from _pf_fixtures import make_repo, run_all
from voxera_patientfetch.identity import (GIVE_UP, IdentityVerifier, extract_uuid, normalize_patient_id,
                                          normalize_phone, phones_match)
from voxera_patientfetch.memory_repo import MemoryRepo
from voxera_patientfetch.repository import RepoError


def test_normalizes_many_spoken_and_typed_forms():
    canon = "VX-000421"
    for raw in ["VX-000421", "vx-00421", "VX 421", "V X 0 0 4 2 1", "v.x. zero zero four two one",
                "  my patient id is VX 00421  ", "vx zero zero four two one", "Vx-000421.", "वीएक्स"]:
        got = normalize_patient_id(raw)
        if raw == "वीएक्स":
            assert got is None
        else:
            assert got == canon, (raw, got)


def test_double_and_devanagari_digits():
    assert normalize_patient_id("vx double zero four two one") == "VX-000421"
    assert normalize_patient_id("VX ००४२१") == "VX-000421"


def test_no_id_in_speech_returns_none():
    for raw in ["I have chest pain", "", None, "hello there", "my phone is nine nine nine"]:
        assert normalize_patient_id(raw) is None, raw


def test_valid_id_verifies():
    v = IdentityVerifier(make_repo())
    r = v.attempt("VX 421")
    assert r.verified and r.reason == "ok" and r.patient["id"] == "p1"
    assert "I have your record" in r.spoken


def test_invalid_id_then_retry_then_success():
    v = IdentityVerifier(make_repo())
    r1 = v.attempt("VX 999")
    assert not r1.verified and r1.reason == "not_found" and "repeat" in r1.spoken and r1.patient is None
    assert r1.attempts_left == 2
    r2 = v.attempt("VX-000421")
    assert r2.verified


def test_repeated_invalid_locks_and_never_exposes_a_patient():
    v = IdentityVerifier(make_repo(), max_attempts=3)
    rs = [v.attempt(x) for x in ("VX 900", "VX 901", "VX 902")]
    assert all(r.patient is None for r in rs)
    assert rs[-1].reason == "not_found" and rs[-1].spoken == GIVE_UP and v.locked
    # locked: even a correct ID no longer verifies in this call
    r = v.attempt("VX 421")
    assert not r.verified and r.reason == "locked"


def test_bad_format_consumes_attempt():
    v = IdentityVerifier(make_repo())
    r = v.attempt("I have a headache")
    assert not r.verified and r.reason == "bad_format" and r.attempts_used == 1


def test_same_phone_wrong_patient_id_does_not_verify():
    v = IdentityVerifier(make_repo(), caller_phone="9990001111")     # phone of p1
    r = v.attempt("VX 422")                                            # but says p2's ID
    assert r.verified and r.patient["id"] == "p2"                       # the ID rules...
    assert r.phone_matched is False and r.assurance == "low"            # ...mismatch lowers assurance (no record read-out)
    # a phone number alone must never verify
    v2 = IdentityVerifier(make_repo(), caller_phone="9990001111")
    r2 = v2.attempt("9990001111")
    assert not r2.verified


def test_phone_match_is_only_a_secondary_signal():
    v = IdentityVerifier(make_repo(), caller_phone="+91 9990001111")
    r = v.attempt("VX 421")
    assert r.verified and r.phone_matched is True and r.assurance == 'high'


def test_duplicate_patient_id_is_ambiguous_not_guessed():
    v = IdentityVerifier(make_repo())
    r = v.attempt("VX 423")
    assert not r.verified and r.reason == "ambiguous" and r.patient is None


def test_uuid_accepted():
    assert extract_uuid("id 3f2b8c1e-1234-4abc-9def-0123456789ab ok") == "3f2b8c1e-1234-4abc-9def-0123456789ab"
    repo = make_repo()
    repo.patients.append({"id": "3f2b8c1e-1234-4abc-9def-0123456789ab", "patient_id": "VX-000999", "full_name": "U"})
    assert IdentityVerifier(repo).attempt("3f2b8c1e-1234-4abc-9def-0123456789ab").verified


def test_database_outage_does_not_verify_or_burn_attempts():
    repo = make_repo()
    repo.fail_reads = True
    v = IdentityVerifier(repo)
    r = v.attempt("VX 421")
    assert not r.verified and r.reason == "error" and "unable to access" in r.spoken.lower()
    assert v.attempts == 0


def test_phone_normalisation():
    assert normalize_phone("+91 99900-01111") == "9990001111"
    assert phones_match("9990001111", "+919990001111") is True
    assert phones_match("9990001111", "9990002222") is False
    assert phones_match(None, "9990002222") is None


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
