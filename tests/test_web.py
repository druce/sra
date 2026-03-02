import pytest
from pathlib import Path


def test_list_reports_empty(tmp_path):
    """Returns empty list when work/ has no completed reports."""
    from web import list_reports
    assert list_reports(tmp_path) == []


def test_list_reports_finds_completed(tmp_path):
    """Returns entry for each work dir that has artifacts/final_report.md."""
    from web import list_reports
    for name in ["ADSK_20260301", "MNDY_20260228"]:
        report = tmp_path / name / "artifacts" / "final_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Report")
    result = list_reports(tmp_path)
    assert len(result) == 2
    syms = {r["ticker"] for r in result}
    assert syms == {"ADSK", "MNDY"}


def test_list_reports_sorted_descending(tmp_path):
    """Reports sorted newest first."""
    from web import list_reports
    for name in ["ADSK_20260101", "ADSK_20260301", "ADSK_20260201"]:
        report = tmp_path / name / "artifacts" / "final_report.md"
        report.parent.mkdir(parents=True)
        report.write_text("# Report")
    result = list_reports(tmp_path)
    dates = [r["date"] for r in result]
    assert dates == sorted(dates, reverse=True)


def test_list_reports_skips_incomplete(tmp_path):
    """Dirs without final_report.md are ignored."""
    from web import list_reports
    incomplete = tmp_path / "COIN_20260301"
    incomplete.mkdir()
    assert list_reports(tmp_path) == []


def test_load_sort_order(tmp_path):
    """Parses sort_order from DAG YAML into {task_id: int} dict."""
    from web import load_sort_order
    dag = tmp_path / "test.yaml"
    dag.write_text("""
dag:
  version: 2
  name: Test
tasks:
  profile:
    sort_order: 1
    type: python
    config:
      script: x.py
  technical:
    sort_order: 2
    type: python
    config:
      script: x.py
  no_order_task:
    type: python
    config:
      script: x.py
""")
    order = load_sort_order(dag)
    assert order == {"profile": 1, "technical": 2, "no_order_task": 999}
