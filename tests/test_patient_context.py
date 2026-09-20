#   python tests/test_patient_context.py
import sys

from _pf_fixtures import make_repo, make_svc, run_all
from voxera_patientfetch.repository import RepoError


def test_context_loads_minimum_necessary_snapshot():
    ctx = make_svc().build_clinical_context("p1")
    assert ctx.patient["patient_id"] == "VX-000421" and ctx.patient["first_name"] == "Voxera"
    assert "full_name" not in ctx.patient and "phone" not in ctx.patient       # PHI minimised
    assert [m["medicine_name"] for m in ctx.active_medications] == ["Budecort"]
    unv = {m["medicine_name"] for m in ctx.unverified_medications}
    assert unv == {"Montelukast", "Crocin"}                                     # kept apart from active
    assert len(ctx.previous_consultations) == 2 and ctx.appointments and ctx.recent_referrals


def test_allergies_merge_verified_record_and_patient_reported():
    ctx = make_svc().build_clinical_context("p1")
    assert "Penicillin" in ctx.allergies and "dust" in ctx.allergies
    assert "Asthma" in ctx.conditions


def test_llm_view_is_tiny_and_has_no_identity():
    v = make_svc().build_clinical_context("p1").llm_view()
    blob = str(v).lower()
    assert "voxera demo" not in blob and "9990" not in blob and "vx-" not in blob
    assert v["active_medications"] == ["Budecort"]


def test_context_is_cached_for_the_call_and_invalidated_on_change():
    svc = make_svc()
    svc.build_clinical_context("p1")
    reads = svc.repo.reads
    svc.build_clinical_context("p1")
    assert svc.repo.reads == reads                       # served from cache, zero DB hits
    svc.confirm_medication("it1", user_id="u", role="doctor", facility_id="f")
    ctx = svc.build_clinical_context("p1")
    assert svc.repo.reads > reads
    assert {"Budecort", "Montelukast"} == {m["medicine_name"] for m in ctx.active_medications}


def test_supabase_outage_raises_instead_of_inventing_a_record():
    repo = make_repo()
    repo.fail_reads = True
    try:
        make_svc(repo).build_clinical_context("p1")
        raise AssertionError("context built during outage")
    except RepoError:
        pass


def test_missing_tables_degrade_to_a_partial_context_not_a_crash():
    repo = make_repo()
    repo.missing.add("prescriptions")
    ctx = make_svc(repo).build_clinical_context("p1")
    assert ctx.partial is True


def test_summary_counts():
    s = make_svc().get_patient_summary("p1")
    assert s["active_medications"] == 1 and s["unverified_medications"] == 2 and s["allergies"] >= 1 and s["last_consultation"]


if __name__ == "__main__":
    sys.exit(1 if run_all(dict(globals())) else 0)
