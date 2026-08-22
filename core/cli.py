"""`cdp` command-line interface."""

from __future__ import annotations

import json
import logging
import shutil

import click

from core.config import get_settings


@click.group()
def main() -> None:
    """Agentic CDP: identity-resolution pipeline and review app."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@main.command()
@click.option("--seed", type=int, default=None, help="Override the generator seed.")
@click.option("--companies", type=int, default=None, help="Override the company count.")
def generate(seed: int | None, companies: int | None) -> None:
    """Generate the synthetic sources, ground truth, and enrichment reference."""
    from core.pipeline import run_generate

    settings = get_settings()
    if seed is not None:
        settings = settings.model_copy(update={"seed": seed})
    if companies is not None:
        settings = settings.model_copy(update={"n_companies": companies})
    summary = run_generate(settings)
    click.echo(json.dumps(summary, indent=2))


@main.command()
@click.option("--record-mock", is_flag=True, help="Commit the mock agent decisions as artifacts.")
@click.option(
    "--finalize-pending", is_flag=True,
    help="Hand still-pending queue items to the oracle bulk reviewer (labeled).",
)
def pipeline(record_mock: bool, finalize_pending: bool) -> None:
    """Run bronze → silver → matching stages → clustering → gold → eval."""
    from core.pipeline import run_pipeline

    summary = run_pipeline(get_settings(), record_mock=record_mock, finalize_pending=finalize_pending)
    click.echo(json.dumps(summary, indent=2, default=str))


@main.command(name="eval")
def eval_cmd() -> None:
    """Print the current metrics snapshot."""
    settings = get_settings()
    if not settings.metrics_path.exists():
        raise click.ClickException("No metrics yet — run `cdp pipeline` first.")
    click.echo(settings.metrics_path.read_text())


@main.command()
@click.option("--port", type=int, default=8000)
@click.option("--host", default="127.0.0.1")
@click.option(
    "--fixture", type=click.Path(exists=True), default=None,
    help="Reset queue.db and seed it from a JSON fixture before serving (tests/demo).",
)
def serve(port: int, host: str, fixture: str | None) -> None:
    """Serve the review-queue API and the built frontend."""
    import uvicorn

    from app.server.main import create_app, seed_from_fixture

    settings = get_settings()
    if fixture:
        seed_from_fixture(settings, fixture)
    uvicorn.run(create_app(settings), host=host, port=port, log_level="info")


@main.command()
@click.option("--limit", type=int, default=8)
def peek(limit: int) -> None:
    """Show a sample of messy source records for the same company (demo helper)."""
    from adapters import local_store

    settings = get_settings()
    con = local_store.connect(settings.duckdb_path)
    try:
        gt_path = settings.artifacts_dir / "ground_truth.parquet"
        if not gt_path.exists() or con.execute("SELECT COUNT(*) FROM silver_records").fetchone()[0] == 0:
            raise click.ClickException("Run `cdp generate` and `cdp pipeline` first.")
        con.execute(f"CREATE OR REPLACE TEMP VIEW gt AS SELECT * FROM read_parquet('{gt_path.as_posix()}')")
        # Pick an entity that appears in several sources with varied names.
        df = con.execute(
            """
            WITH picked AS (
                SELECT entity_id FROM gt JOIN silver_records USING (record_id)
                GROUP BY entity_id
                HAVING COUNT(*) >= 3 AND COUNT(DISTINCT company_name) >= 3
                ORDER BY entity_id LIMIT 1
            )
            SELECT s.source_system, s.company_name, s.domain, s.phone, s.address
            FROM silver_records s JOIN gt USING (record_id)
            WHERE gt.entity_id = (SELECT entity_id FROM picked)
            ORDER BY s.source_system
            LIMIT ?
            """,
            [limit],
        ).df()
        click.echo("Same company, four source systems:")
        click.echo(df.to_string(index=False))
    finally:
        con.close()


@main.command(name="governance-demo")
def governance_demo() -> None:
    """Create masked (analyst) vs clear (steward) views over gold and show the difference."""
    from adapters import local_store

    settings = get_settings()
    con = local_store.connect(settings.duckdb_path)
    try:
        con.execute(
            """
            CREATE OR REPLACE VIEW vw_profiles_steward AS
            SELECT customer_id, canonical_name, canonical_domain, canonical_address,
                   canonical_phone, industry, employees, revenue, industry_code, employee_band
            FROM gold_profiles
            """
        )
        con.execute(
            """
            CREATE OR REPLACE VIEW vw_profiles_analyst AS
            SELECT customer_id, canonical_name, canonical_domain,
                   CASE WHEN canonical_address IS NULL THEN NULL ELSE '•••' END AS canonical_address,
                   CASE WHEN canonical_phone   IS NULL THEN NULL ELSE '•••' END AS canonical_phone,
                   industry, employees, revenue, industry_code, employee_band
            FROM gold_profiles
            """
        )
        click.echo("Views created: vw_profiles_steward (clear) / vw_profiles_analyst (masked).\n")
        for view in ("vw_profiles_steward", "vw_profiles_analyst"):
            click.echo(f"-- {view} (first 3 rows)")
            click.echo(con.execute(f"SELECT * FROM {view} LIMIT 3").df().to_string(index=False))
            click.echo("")
    finally:
        con.close()


@main.command()
@click.confirmation_option(prompt="Delete all local pipeline data (data/local)?")
def reset() -> None:
    """Delete the local DuckDB/SQLite files and metrics (artifacts are kept)."""
    settings = get_settings()
    if settings.cdp_data_dir.exists():
        shutil.rmtree(settings.cdp_data_dir)
    settings.cdp_data_dir.mkdir(parents=True, exist_ok=True)
    click.echo(f"Cleared {settings.cdp_data_dir}")
