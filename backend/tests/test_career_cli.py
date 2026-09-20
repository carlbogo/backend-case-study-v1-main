from cli import app
from typer.testing import CliRunner

from src.career_details._matching.service import CareerMatchingService
from src.career_details._matching.views import ReconciliationResult


def test_cli_commands_remain_available_and_reconciliation_uses_selected_account(monkeypatch):
    runner = CliRunner()
    for command in (["--help"], ["cv", "import", "--help"], ["career-details", "set", "--help"]):
        assert runner.invoke(app, command).exit_code == 0
    accounts = []

    async def reconcile(self, user_id):
        accounts.append(user_id)
        return ReconciliationResult(cvs_processed=2, entries_linked=2, conflicts_preserved=1)

    monkeypatch.setattr(CareerMatchingService, "reconcile_account", reconcile)
    result = runner.invoke(app, ["career-details", "reconcile", "--user", "bob"])
    assert result.exit_code == 0, result.output
    assert accounts == ["bob"]
    assert "Processed 2 CVs" in result.output
    assert "preserved 1 conflicting" in result.output
