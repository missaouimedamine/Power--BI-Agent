"""``powerbi-agent`` command-line interface.

Every command plans its work through the orchestrator. Capabilities that have not been
implemented yet are reported as ``not_implemented`` with a non-zero exit code; the CLI
never pretends work was done.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from powerbi_agent import __version__
from powerbi_agent.core.agent import default_registry
from powerbi_agent.core.events import EventBus, log_event
from powerbi_agent.core.orchestrator import Orchestrator
from powerbi_agent.core.planner import RuleBasedPlanner, build_plan
from powerbi_agent.core.state import StateStore
from powerbi_agent.models.datasource import DataSource, DataSourceType
from powerbi_agent.models.tasks import Capability, Intent, Plan, PlanStep, Task, TaskStatus
from powerbi_agent.utils.config import Settings, get_settings
from powerbi_agent.utils.logging import configure_logging

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INCOMPLETE = 2  # not implemented, cancelled or skipped
EXIT_BAD_INPUT = 3

app = typer.Typer(
    name="powerbi-agent",
    help="AI data analyst and Power BI semantic model / report generation agent.",
    no_args_is_help=True,
    add_completion=False,
)
out = Console()
err = Console(stderr=True)

JsonOpt = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
WorkspaceOpt = Annotated[
    Path | None,
    typer.Option("--workspace", "-w", help="Output workspace (default: workspaces/<name>)."),
]


def _settings() -> Settings:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    return settings


def _print_plan(plan: Plan) -> None:
    table = Table(title=f"Plan ({plan.intent.value})", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Capability")
    table.add_column("Description")
    table.add_column("Risk")
    for i, step in enumerate(plan.steps, 1):
        risk = step.risk.value + (" (confirm)" if step.risk.requires_confirmation else "")
        table.add_row(str(i), step.capability.value, step.description, risk)
    out.print(table)
    for note in plan.notes:
        out.print(f"[yellow]note:[/yellow] {note}")


def _print_task(task: Task) -> None:
    table = Table(title=f"Task {task.id}: {task.status.value}")
    table.add_column("Capability")
    table.add_column("Status")
    table.add_column("Agent")
    table.add_column("Detail")
    for r in task.results:
        colour = {"succeeded": "green", "failed": "red"}.get(r.status.value, "yellow")
        table.add_row(
            r.capability.value, f"[{colour}]{r.status.value}[/]", r.agent or "-", r.error or ""
        )
    out.print(table)


def _exit_code(status: TaskStatus) -> int:
    if status is TaskStatus.SUCCEEDED:
        return EXIT_OK
    if status is TaskStatus.FAILED:
        return EXIT_FAILED
    return EXIT_INCOMPLETE


def _confirm_step(plan: Plan, step: PlanStep) -> bool:
    return typer.confirm(
        f"Step '{step.description}' is {step.risk.value}. Proceed?", default=False, err=True
    )


def _fmt_kpi(value: float | None, fmt: str) -> str:
    if value is None:
        return "n/a"
    if fmt == "percent":
        return f"{value:.1%}"
    if fmt == "integer" or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _print_summary(task: Task) -> None:
    """The "I found ..." summary, built only from tool-reported step outputs."""
    outputs = {r.capability: r.output for r in task.results if r.status is TaskStatus.SUCCEEDED}
    lines: list[str] = []
    if load := outputs.get(Capability.LOAD_DATA):
        sheet = f" (sheet {load['sheet']})" if load.get("sheet") else ""
        lines.append(
            f"Dataset {load['dataset']}{sheet}: {load['rows']:,} rows, {load['columns']} columns"
        )
        lines += [f"  note: {n}" for n in load.get("notes", [])]
    if prof := outputs.get(Capability.PROFILE_DATA):
        lines.append(
            f"Missing cells: {prof['missing_values']:,}; duplicate rows: {prof['duplicate_rows']:,}"
        )
    if ins := outputs.get(Capability.GENERATE_INSIGHTS):
        lines.append(f"Candidate dimensions: {', '.join(ins['candidate_dimensions']) or '-'}")
        lines.append(f"Candidate measures: {', '.join(ins['candidate_measures']) or '-'}")
        kpis = ", ".join(f"{k['name']} = {_fmt_kpi(k['value'], k['format'])}" for k in ins["kpis"])
        lines.append(f"Suggested KPIs: {kpis or '-'}")
    if qual := outputs.get(Capability.CHECK_QUALITY):
        lines.append(
            f"Data quality: {qual['errors']} error(s), {qual['warnings']} warning(s), "
            f"{qual['info']} info"
        )
        lines += [
            f"  - {i['kind']}"
            + (f" in {i['column']}" if i["column"] else "")
            + f": {i['rows']:,} rows"
            for i in qual["issues"]
            if i["rows"]
        ][:10]
    if design := outputs.get(Capability.DESIGN_STAR_SCHEMA):
        lines.append("")
        lines.append(f"Proposed model {design['model']} (grain: {' + '.join(design['grain'])}):")
        lines += [
            f"  {t['name']:<14} {t['kind']:<10} {t['rows']:>10,} rows" for t in design["tables"]
        ]
        lines.append(f"Relationships: {len(design['relationships'])}")
        lines += [f"  {r}" for r in design["relationships"]]
        lines += [f"Hierarchy {h}" for h in design["hierarchies"]]
        if design["findings"]:
            lines.append(f"Modeling findings ({len(design['findings'])}):")
            lines += [f"  - [{f['severity']}] {f['message']}" for f in design["findings"]]
        lines.append("Changes: " + "; ".join(design["changes"]))
    checks = [r for r in task.results if r.capability is Capability.VALIDATE_MODEL and r.output]
    for r in checks:
        o = r.output
        lines.append(
            f"Model validation: {'passed' if o['passed'] else 'FAILED'} "
            f"({o['checks_passed']} passed, {o['checks_failed']} failed, "
            f"{o['checks_not_run']} not run)"
        )
    artifacts = [a for r in task.results for a in r.artifacts]
    if artifacts:
        lines.append(
            f"Artifacts ({len(artifacts)}): "
            + ", ".join(artifacts[:8])
            + (" ..." if len(artifacts) > 8 else "")
        )
    if lines:
        out.print()
        out.print("[bold]I found:[/bold]")
        for line in lines:
            out.print(line, markup=False, highlight=False)
    if design:
        out.print(
            "\nNothing has been written to Power BI. I will not modify a Power BI model "
            "until you confirm.",
            markup=False,
        )


def _run(
    intent: Intent, request: str, sources: list[DataSource], workspace: Path, as_json: bool
) -> None:
    settings = _settings()
    bus = EventBus()
    bus.subscribe(log_event)
    orchestrator = Orchestrator(
        default_registry(),
        settings,
        bus=bus,
        confirm=_confirm_step,
        state=StateStore(workspace),
    )
    task = Task(request=request, plan=build_plan(request, intent, sources))
    task = orchestrator.execute(task, workspace)
    if as_json:
        out.print_json(task.model_dump_json())
    else:
        _print_task(task)
        _print_summary(task)
        if task.status is TaskStatus.NOT_IMPLEMENTED:
            out.print(
                "[yellow]Later steps are not implemented yet (see docs/architecture.md, "
                "'Implementation status'). They produced no artifacts.[/]"
            )
    raise typer.Exit(_exit_code(task.status))


def _data_command(
    intent: Intent,
    data_file: Path | None,
    workspace: Path | None,
    as_json: bool,
    *,
    sheet: str | None = None,
    query: str | None = None,
    connection_env: str | None = None,
) -> None:
    try:
        if query or connection_env:
            if data_file is not None or not (query and connection_env):
                raise ValueError("SQL sources need --query and --connection-env and no file")
            source = DataSource(
                name="query", type=DataSourceType.SQL, query=query, connection_env=connection_env
            )
        else:
            if data_file is None:
                raise ValueError("give a data file, or --query with --connection-env")
            if not data_file.is_file():
                raise ValueError(f"file not found: {data_file}")
            source = DataSource.from_path(data_file, sheet=sheet)
    except ValueError as exc:
        err.print(f"[red]error:[/] {exc}")
        raise typer.Exit(EXIT_BAD_INPUT) from exc
    settings = get_settings()
    ws = workspace or settings.workspaces_dir / source.name.lower()
    _run(intent, f"{intent.value} {data_file or 'SQL query'}", [source], ws, as_json)


def _workspace_command(intent: Intent, workspace: Path, as_json: bool) -> None:
    if not workspace.is_dir():
        err.print(f"[red]error:[/] workspace not found: {workspace}")
        raise typer.Exit(EXIT_BAD_INPUT)
    _run(intent, f"{intent.value} {workspace}", [], workspace, as_json)


DataArg = Annotated[
    Path | None, typer.Argument(help="CSV, Excel or Parquet file (omit for --query).")
]
WorkspaceArg = Annotated[Path, typer.Argument(help="Generated workspace directory.")]
SheetOpt = Annotated[str | None, typer.Option("--sheet", help="Excel sheet (default: first).")]
QueryOpt = Annotated[str | None, typer.Option("--query", help="Read-only SQL query (SELECT/WITH).")]
ConnEnvOpt = Annotated[
    str | None,
    typer.Option(
        "--connection-env", help="Name of the env var holding the SQLAlchemy URL (not the URL)."
    ),
]


@app.command()
def analyze(
    data_file: DataArg = None,
    workspace: WorkspaceOpt = None,
    sheet: SheetOpt = None,
    query: QueryOpt = None,
    connection_env: ConnEnvOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Profile, quality-check and summarise a dataset (writes analysis/)."""
    _data_command(
        Intent.ANALYZE,
        data_file,
        workspace,
        as_json,
        sheet=sheet,
        query=query,
        connection_env=connection_env,
    )


@app.command()
def profile(
    data_file: DataArg = None,
    workspace: WorkspaceOpt = None,
    sheet: SheetOpt = None,
    query: QueryOpt = None,
    connection_env: ConnEnvOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Profile a dataset's columns, types and roles (writes profile.json, schema.json)."""
    _data_command(
        Intent.PROFILE,
        data_file,
        workspace,
        as_json,
        sheet=sheet,
        query=query,
        connection_env=connection_env,
    )


@app.command("design-model")
def design_model(
    data_file: DataArg = None,
    workspace: WorkspaceOpt = None,
    sheet: SheetOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Propose a star-schema semantic model spec (no files written to Power BI)."""
    _data_command(Intent.DESIGN_MODEL, data_file, workspace, as_json, sheet=sheet)


@app.command("generate-model")
def generate_model(
    data_file: DataArg = None,
    workspace: WorkspaceOpt = None,
    sheet: SheetOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Generate a PBIP project with a TMDL semantic model."""
    _data_command(Intent.GENERATE_MODEL, data_file, workspace, as_json, sheet=sheet)


@app.command("generate-report")
def generate_report(
    data_file: DataArg = None,
    workspace: WorkspaceOpt = None,
    sheet: SheetOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Generate the model plus a report specification."""
    _data_command(Intent.GENERATE_REPORT, data_file, workspace, as_json, sheet=sheet)


@app.command()
def validate(workspace: WorkspaceArg, as_json: JsonOpt = False) -> None:
    """Validate a generated workspace and print a structured report."""
    _workspace_command(Intent.VALIDATE, workspace, as_json)


@app.command()
def publish(workspace: WorkspaceArg, as_json: JsonOpt = False) -> None:
    """Publish a validated workspace to Power BI / Fabric (requires confirmation)."""
    settings = _settings()
    if not settings.allow_publish:
        err.print(
            "[red]Publishing is disabled.[/] It is not implemented yet (Phase 8) and "
            "requires ALLOW_PUBLISH=true once it is. Nothing was sent to Power BI."
        )
        raise typer.Exit(EXIT_INCOMPLETE)
    _workspace_command(Intent.PUBLISH, workspace, as_json)


@app.command()
def plan(request: Annotated[str, typer.Argument(help="Natural-language request.")]) -> None:
    """Show the plan the orchestrator would follow for a request (nothing is executed)."""
    _print_plan(RuleBasedPlanner().plan(request))


@app.command()
def chat() -> None:
    """Interactive session. Phase 1: plans requests only; no LLM is connected yet."""
    _settings()
    planner = RuleBasedPlanner()
    out.print("powerbi-agent chat (Phase 1: planning only). Type 'exit' to quit.")
    while True:
        try:
            request = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if request.lower() in {"exit", "quit"}:
            break
        if request:
            _print_plan(planner.plan(request))


@app.command()
def config(as_json: JsonOpt = False) -> None:
    """Show effective configuration with secrets redacted."""
    data = get_settings().redacted()
    if as_json:
        out.print_json(json.dumps(data, default=str))
        return
    table = Table(title="Configuration")
    table.add_column("Setting")
    table.add_column("Value")
    for key, value in data.items():
        table.add_row(key, str(value))
    out.print(table)


@app.command()
def version() -> None:
    """Print the package version."""
    out.print(__version__)


if __name__ == "__main__":
    app()
