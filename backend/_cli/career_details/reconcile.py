import typer

from src.career_details._matching.service import CareerMatchingService


async def reconcile(user: str = typer.Option("alice", help="Local user ID, e.g. alice or bob.")):
    """Reconcile existing CVs after migration, or retry previously uncertain matches."""
    result = await CareerMatchingService().reconcile_account(user)
    typer.echo(
        f"Processed {result.cvs_processed} CVs; changed {result.entries_linked} employment links; "
        f"preserved {result.conflicts_preserved} conflicting detail records."
    )
