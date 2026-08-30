# deriv-test-agent

Simple ReAct agent
Agent generated with `agents-cli` version `1.4.1`

## Project Structure

```
deriv-test-agent/
├── app/         # Core agent code
│   ├── agent.py               # Main agent logic
│   ├── fast_api_app.py        # FastAPI Backend server
│   └── app_utils/             # App utilities and helpers
├── tests/                     # Unit, integration, and load tests
├── AGENTS.md                  # Cursor / coding-agent development guide
├── GEMINI.md                  # Pointer to AGENTS.md for Gemini CLI
├── .cursor/rules/             # Cursor rules (guidelines, eval, deploy)
└── pyproject.toml             # Project dependencies
```

> **Tip:** Project context for coding agents is in `AGENTS.md` and `.cursor/rules/`. `GEMINI.md` points at those files.

## Requirements

Before you begin, ensure you have:
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)


## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Incident command pipeline

Staged incident workflow (group → severity → actions → safety → drafts → TTY review → re-decision).

```bash
# Interactive live run (Vertex + TTY operator review)
uv run python -m app.pipeline --pair public

# First-pass only (stop after stakeholder drafts; skip TTY)
uv run python -m app.pipeline --pair public --until stakeholder

# Resume: if first-pass JSON already exists, skip Vertex and open operator review
uv run python -m app.pipeline --pair public

# Force a fresh first pass even when artifacts exist
uv run python -m app.pipeline --pair public --rerun

# Offline / CI-friendly run (fake LLM, auto-accept reviews)
uv run python -m app.pipeline --pair public --fake-llm --skip-review

# Validate pair inputs + artifacts
uv run python validate.py --pair public
uv run python validate.py --pair public --run-fake

# CI eval (14 stage cases, each graded by five metrics)
# Regenerate the dataset first so the grounding context matches current fixtures.
uv run python tests/eval/make_pipeline_dataset.py --pair public
agents-cli eval run \
  --config tests/eval/eval_config_pipeline.yaml \
  --dataset tests/eval/datasets/pipeline-dataset.json \
  --concurrency 1

# Artifact contracts only (no Vertex/ADC needed)
agents-cli eval grade \
  --traces <trace-file> \
  --config tests/eval/eval_config_artifacts.yaml
```

`agents-cli eval run` needs Vertex credentials (`gcloud auth application-default login`).
`grounding_v1` scores the answer against the eval case `context`, so
`make_pipeline_dataset.py` fills that field with the live `summarize_pair` output;
run it after the pipeline whenever the input fixtures change.
The generated cases cover inputs and lifecycle order, grouping, severity,
actions, deterministic safety, stakeholder drafts, operator review,
feedback-driven re-decision, comparison, analytics, prior feedback, escalation,
LLM audit logs, and the final overview.

Pair folders live under `pipeline_files/<pair>/` (pair name is also the session id). Sample inputs are in `pipeline_files/public/`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `uv run pre-commit install` | Install git hooks (pre-commit + commit-msg)                                     |
| `uv run cz commit`   | Interactive conventional commit (Commitizen)                                          |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        |
| `agents-cli deploy`  | Deploy agent to Agent Runtime                                                                |
| `agents-cli publish gemini-enterprise` | Register deployed agent to Gemini Enterprise                    || [A2A Inspector](https://github.com/a2aproject/a2a-inspector) | Launch A2A Protocol Inspector                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `app/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

### Git hooks and conventional commits

Install lint extras (includes pre-commit and Commitizen), then install hooks:

```bash
uv sync --extra lint
uv run pre-commit install
```

`pre-commit install` also registers the `commit-msg` hook (see `default_install_hook_types` in `.pre-commit-config.yaml`). Commit messages are checked by **Commitizen**.

Create a conventional commit interactively:

```bash
uv run cz commit
```

Bump the version and changelog from conventional commits:

```bash
uv run cz bump
```

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.

## A2A Inspector

This agent supports the [A2A Protocol](https://a2a-protocol.org/). Use the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) to test interoperability.
See the [A2A Inspector docs](https://github.com/a2aproject/a2a-inspector) for details.
