# Setup

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.12+ | everything |
| [uv](https://docs.astral.sh/uv/) | 0.4+ | environment management (pip also works) |
| Git | any recent | versioning generated projects |
| Node.js | 20+ (`npx`) | Power BI Modeling MCP server (Phase 5+) |
| [OpenCode](https://opencode.ai) | latest | LLM agent runtime (optional; the CLI works without it) |
| Power BI Desktop | recent, Windows | opening generated PBIP projects (PBIP save is a preview feature) |

## Install

```bash
git clone <repo> ai-powerbi-agent && cd ai-powerbi-agent
make install            # or: uv venv --python 3.12 .venv && uv pip install -e ".[dev]"
cp .env.example .env    # then edit
```

Windows without `make`:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
Copy-Item .env.example .env
```

Optional extras: `.[viz]` (matplotlib, plotly; needed for `analysis/charts/`), `.[api]`
(FastAPI), `.[mcp]` (MCP SDK). Without `viz`, analysis still runs and records a note
that charts were skipped.

SQL sources need the SQLAlchemy driver for your database (e.g. `psycopg`, `pyodbc`).
Put the URL in `.env` under any name and pass that **name**:

```bash
powerbi-agent analyze --query "SELECT * FROM sales" --connection-env SALES_DB_URL
```

Check the install:

```bash
powerbi-agent version
powerbi-agent config          # secrets are shown as **********
powerbi-agent plan "Analyze sales.xlsx and build a sales dashboard"
```

## OpenCode

1. Install OpenCode and configure a model provider using OpenCode's own auth
   (`opencode auth login`). The project does not store LLM keys in its config files.
2. Point OpenCode at the project config:

   ```bash
   export OPENCODE_CONFIG=$PWD/opencode/config/opencode.json   # PowerShell: $env:OPENCODE_CONFIG=...
   make opencode-install   # copies skills into .opencode/skill/ for discovery
   opencode
   ```

3. Choose the `orchestrator` agent.

> OpenCode was **not installed** in the environment where Phase 1 was built. The config
> follows the published OpenCode schema (`mcp`, `agent`, `permission`, `{file:...}`
> prompt references) but has not been loaded by a real OpenCode binary yet. Check it
> with your OpenCode version, especially the skills discovery directory.

## Power BI authentication

Two separate paths, both configured without putting secrets in code:

### 1. Power BI Modeling MCP (model authoring, Phase 5)

- Default: **interactive** sign-in through the Azure Identity SDK when the server first
  connects to a Fabric workspace. Power BI Desktop and local PBIP folders need no cloud
  sign-in.
- Service principal (CI/automation): the server reads `AZURE_TENANT_ID`,
  `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (or certificate variables) from its own
  environment. Set them in the shell that launches OpenCode, not in `opencode.json`.
- Requires **Write** permission on the semantic model and XMLA endpoint access in the
  tenant/capacity.
- Enable it in `opencode/config/opencode.json` (`"enabled": true`) and set
  `POWERBI_ENABLED=true` in `.env`.

### 2. Fabric / Power BI REST (publish/refresh, Phase 8)

1. Register an app in Microsoft Entra ID; create a client secret or certificate.
2. In the Fabric admin portal, allow service principals to use Fabric APIs (scope it to a
   security group).
3. Add the service principal to the target workspace as **Contributor** or higher.
4. Fill `POWERBI_TENANT_ID`, `POWERBI_CLIENT_ID`, `POWERBI_CLIENT_SECRET`,
   `POWERBI_WORKSPACE_ID` in `.env`.
5. Publishing also needs `ALLOW_PUBLISH=true` **and** interactive confirmation.

Use a dedicated development workspace. Never point the agent at production
credentials during development.
