"""Tests for DAG schema v2 Pydantic models."""

import pytest
from schema import OutputDef, SetsVarDef, DagHeader
from schema import PythonConfig, ClaudeConfig, ShellConfig
from schema import DagFile


def test_output_def_valid():
    out = OutputDef(path="artifacts/profile.json", format="json")
    assert out.path == "artifacts/profile.json"
    assert out.format == "json"
    assert out.description == ""


def test_output_def_with_description():
    out = OutputDef(path="artifacts/profile.json", format="json", description="Company identity and valuation snapshot")
    assert out.description == "Company identity and valuation snapshot"


def test_output_def_missing_path():
    with pytest.raises(Exception):
        OutputDef(format="json")


def test_output_def_missing_format():
    with pytest.raises(Exception):
        OutputDef(path="artifacts/profile.json")


def test_sets_var_def_valid():
    sv = SetsVarDef(artifact="artifacts/profile.json", key="company_name")
    assert sv.artifact == "artifacts/profile.json"
    assert sv.key == "company_name"


def test_sets_var_def_missing_artifact():
    with pytest.raises(Exception):
        SetsVarDef(key="company_name")


def test_sets_var_def_missing_key():
    with pytest.raises(Exception):
        SetsVarDef(artifact="artifacts/profile.json")


def test_dag_header_valid():
    header = DagHeader(
        version=2,
        name="Test DAG",
        inputs={"ticker": "${ticker}", "workdir": "${workdir}"},
        root_dir="..",
        template_dir="../templates",
    )
    assert header.version == 2
    assert header.name == "Test DAG"


def test_dag_header_wrong_version():
    with pytest.raises(Exception):
        DagHeader(
            version=1,
            name="Test DAG",
            inputs={},
            root_dir="..",
            template_dir="../templates",
        )


def test_dag_header_defaults():
    header = DagHeader(version=2, name="Test")
    assert header.inputs == {}
    assert header.root_dir == "."
    assert header.template_dir == "templates"


def test_dag_header_drafts_dir_default():
    header = DagHeader(version=2, name="Test")
    assert header.drafts_dir == "drafts"


def test_dag_header_drafts_dir_custom():
    header = DagHeader(version=2, name="Test", drafts_dir="my_drafts")
    assert header.drafts_dir == "my_drafts"


def test_python_config_valid():
    cfg = PythonConfig(script="skills/fetch_profile/fetch_profile.py", args={"ticker": "AAPL"})
    assert cfg.script == "skills/fetch_profile/fetch_profile.py"
    assert cfg.args == {"ticker": "AAPL"}


def test_python_config_missing_script():
    with pytest.raises(Exception):
        PythonConfig(args={"ticker": "AAPL"})


def test_python_config_no_args():
    cfg = PythonConfig(script="skills/run.py")
    assert cfg.args == {}


def test_claude_config_valid():
    cfg = ClaudeConfig(
        prompt="Write a report about ${ticker}",
        system="You are an analyst.",
        model="claude-sonnet-4-6",
        tools=["read", "write"],
        allowed_tools=["Bash(git:*)"],
        disallowed_tools=["Write"],
        permission_mode="bypassPermissions",
        skip_permissions=True,
        max_budget_usd=1.50,
        output_format="json",
        effort="high",
        add_dirs=["../data"],
        mcp_config=["mcp.json"],
    )
    assert cfg.prompt == "Write a report about ${ticker}"
    assert cfg.tools == ["read", "write"]
    assert cfg.allowed_tools == ["Bash(git:*)"]
    assert cfg.disallowed_tools == ["Write"]
    assert cfg.permission_mode == "bypassPermissions"
    assert cfg.skip_permissions is True
    assert cfg.max_budget_usd == 1.50
    assert cfg.output_format == "json"
    assert cfg.effort == "high"
    assert cfg.add_dirs == ["../data"]
    assert cfg.mcp_config == ["mcp.json"]


def test_claude_config_tools_all():
    """tools: 'all' accepted as a string."""
    cfg = ClaudeConfig(prompt="Do it", tools="all")
    assert cfg.tools == "all"


def test_claude_config_tools_empty():
    """tools defaults to empty list."""
    cfg = ClaudeConfig(prompt="Do it")
    assert cfg.tools == []


def test_claude_config_minimal():
    cfg = ClaudeConfig(prompt="Do it")
    assert cfg.system is None
    assert cfg.append_system is None
    assert cfg.model is None
    assert cfg.fallback_model is None
    assert cfg.tools == []
    assert cfg.allowed_tools == []
    assert cfg.disallowed_tools == []
    assert cfg.permission_mode is None
    assert cfg.skip_permissions is False
    assert cfg.max_budget_usd is None
    assert cfg.output_format is None
    assert cfg.json_schema is None
    assert cfg.effort is None
    assert cfg.add_dirs == []
    assert cfg.mcp_config == []


def test_claude_config_append_system():
    cfg = ClaudeConfig(prompt="Do it", append_system="Extra context")
    assert cfg.append_system == "Extra context"
    assert cfg.system is None


def test_claude_config_json_schema_dict():
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    cfg = ClaudeConfig(prompt="Do it", json_schema=schema)
    assert cfg.json_schema == schema


def test_claude_config_json_schema_string():
    cfg = ClaudeConfig(prompt="Do it", json_schema='{"type":"object"}')
    assert cfg.json_schema == '{"type":"object"}'


def test_claude_config_invalid_permission_mode():
    with pytest.raises(Exception):
        ClaudeConfig(prompt="Do it", permission_mode="invalid")


def test_claude_config_invalid_output_format():
    with pytest.raises(Exception):
        ClaudeConfig(prompt="Do it", output_format="xml")


def test_claude_config_invalid_effort():
    with pytest.raises(Exception):
        ClaudeConfig(prompt="Do it", effort="ultra")


def test_claude_config_missing_prompt():
    with pytest.raises(Exception):
        ClaudeConfig()


def test_claude_config_critic_defaults():
    """Critic-optimizer fields default to off."""
    cfg = ClaudeConfig(prompt="Do it")
    assert cfg.critic_prompt is None
    assert cfg.rewrite_prompt is None
    assert cfg.n_iterations == 0
    assert cfg.critic_disallowed_tools == []
    assert cfg.rewrite_disallowed_tools == []


def test_claude_config_critic_fields():
    """Critic-optimizer fields are accepted."""
    cfg = ClaudeConfig(
        prompt="Write section",
        critic_prompt="Critique the draft at ${draft_path}",
        rewrite_prompt="Rewrite based on ${critique_path}",
        n_iterations=2,
        critic_disallowed_tools=["yfinance"],
        rewrite_disallowed_tools=["yfinance", "brave-search"],
    )
    assert cfg.critic_prompt == "Critique the draft at ${draft_path}"
    assert cfg.rewrite_prompt == "Rewrite based on ${critique_path}"
    assert cfg.n_iterations == 2
    assert cfg.critic_disallowed_tools == ["yfinance"]
    assert cfg.rewrite_disallowed_tools == ["yfinance", "brave-search"]


def test_shell_config_valid():
    cfg = ShellConfig(command="pandoc input.md -o output.pdf")
    assert cfg.command == "pandoc input.md -o output.pdf"


def test_shell_config_missing_command():
    with pytest.raises(Exception):
        ShellConfig()


def test_task_python():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Get profile",
        "type": "python",
        "config": {"script": "skills/fetch_profile/fetch_profile.py", "args": {"ticker": "AAPL"}},
        "outputs": {"profile": {"path": "artifacts/profile.json", "format": "json"}},
    })
    assert task.type == "python"
    assert isinstance(task.config, PythonConfig)


def test_task_claude():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Write section",
        "type": "claude",
        "depends_on": ["profile"],
        "config": {
            "prompt": "Write a report",
            "tools": ["read"],
        },
        "outputs": {"section": {"path": "artifacts/section.md", "format": "md"}},
    })
    assert task.type == "claude"
    assert isinstance(task.config, ClaudeConfig)
    assert task.depends_on == ["profile"]


def test_task_claude_tools_all():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Write section",
        "type": "claude",
        "config": {
            "prompt": "Write a report",
            "tools": "all",
        },
    })
    assert task.config.tools == "all"


def test_task_shell():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Convert to PDF",
        "type": "shell",
        "config": {"command": "pandoc in.md -o out.pdf"},
    })
    assert task.type == "shell"
    assert isinstance(task.config, ShellConfig)


def test_task_unknown_type():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    with pytest.raises(Exception):
        adapter.validate_python({
            "description": "Bad task",
            "type": "unknown",
            "config": {"script": "foo.py"},
        })


def test_task_wrong_config_for_type():
    """Python type with claude config should fail."""
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    with pytest.raises(Exception):
        adapter.validate_python({
            "description": "Mismatch",
            "type": "python",
            "config": {"prompt": "This is a claude field"},
        })


def test_task_with_sets_vars():
    from pydantic import TypeAdapter
    from schema import Task, SetsVarDef
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Get profile",
        "type": "python",
        "config": {"script": "skills/fetch_profile/fetch_profile.py", "args": {"ticker": "AAPL"}},
        "outputs": {"profile": {"path": "artifacts/profile.json", "format": "json"}},
        "sets_vars": {
            "symbol": {"artifact": "artifacts/profile.json", "key": "symbol"},
            "company_name": {"artifact": "artifacts/profile.json", "key": "company_name"},
        },
    })
    assert len(task.sets_vars) == 2
    assert isinstance(task.sets_vars["symbol"], SetsVarDef)
    assert task.sets_vars["company_name"].key == "company_name"


def test_task_defaults():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Minimal",
        "type": "shell",
        "config": {"command": "echo hi"},
    })
    assert task.depends_on == []
    assert task.outputs == {}
    assert task.sets_vars == {}


def test_dagfile_valid():
    dag = DagFile(
        dag={"version": 2, "name": "Test"},
        tasks={
            "step1": {
                "description": "First",
                "type": "shell",
                "config": {"command": "echo hello"},
            },
            "step2": {
                "description": "Second",
                "type": "python",
                "depends_on": ["step1"],
                "config": {"script": "run.py"},
            },
        },
    )
    assert len(dag.tasks) == 2
    assert dag.tasks["step2"].depends_on == ["step1"]


def test_dagfile_version_1_rejected():
    with pytest.raises(Exception):
        DagFile(
            dag={"version": 1, "name": "Old"},
            tasks={},
        )


# ---------------------------------------------------------------------------
# Cross-reference validation tests (validate_dag)
# ---------------------------------------------------------------------------

from schema import validate_dag


def test_validate_dag_valid():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "shell", "config": {"command": "echo a"}},
            "b": {"description": "B", "type": "shell", "depends_on": ["a"], "config": {"command": "echo b"}},
        },
    }
    dag = validate_dag(raw)
    assert len(dag.tasks) == 2


def test_validate_dag_bad_dependency_ref():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "shell", "depends_on": ["nonexistent"], "config": {"command": "echo a"}},
        },
    }
    with pytest.raises(ValueError, match="nonexistent"):
        validate_dag(raw)


def test_validate_dag_cycle():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "shell", "depends_on": ["b"], "config": {"command": "echo a"}},
            "b": {"description": "B", "type": "shell", "depends_on": ["a"], "config": {"command": "echo b"}},
        },
    }
    with pytest.raises(ValueError, match="[Cc]ycle"):
        validate_dag(raw)


def test_validate_dag_duplicate_output_paths():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {
                "description": "A",
                "type": "shell",
                "config": {"command": "echo a"},
                "outputs": {"out": {"path": "same.txt", "format": "txt"}},
            },
            "b": {
                "description": "B",
                "type": "shell",
                "config": {"command": "echo b"},
                "outputs": {"out": {"path": "same.txt", "format": "txt"}},
            },
        },
    }
    with pytest.raises(ValueError, match="same.txt"):
        validate_dag(raw)


def test_validate_dag_critic_missing_prompts():
    """n_iterations > 0 without both prompts should fail validation."""
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "write": {
                "description": "Write",
                "type": "claude",
                "config": {
                    "prompt": "Write a section",
                    "n_iterations": 1,
                    "critic_prompt": "Critique it",
                    # rewrite_prompt missing
                },
                "outputs": {"section": {"path": "artifacts/section.md", "format": "md"}},
            },
        },
    }
    with pytest.raises(ValueError, match="rewrite_prompt"):
        validate_dag(raw)


def test_validate_dag_critic_missing_critic_prompt():
    """n_iterations > 0 without critic_prompt should fail."""
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "write": {
                "description": "Write",
                "type": "claude",
                "config": {
                    "prompt": "Write a section",
                    "n_iterations": 1,
                    # critic_prompt missing
                    "rewrite_prompt": "Rewrite it",
                },
                "outputs": {"section": {"path": "artifacts/section.md", "format": "md"}},
            },
        },
    }
    with pytest.raises(ValueError, match="critic_prompt"):
        validate_dag(raw)


def test_validate_dag_critic_zero_iterations_ok():
    """n_iterations=0 with no prompts is fine (default)."""
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "write": {
                "description": "Write",
                "type": "claude",
                "config": {"prompt": "Write a section"},
                "outputs": {"section": {"path": "artifacts/section.md", "format": "md"}},
            },
        },
    }
    dag = validate_dag(raw)
    assert dag.tasks["write"].config.n_iterations == 0


def test_validate_dag_critic_valid_config():
    """Complete critic config passes validation."""
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "write": {
                "description": "Write",
                "type": "claude",
                "config": {
                    "prompt": "Write a section",
                    "n_iterations": 1,
                    "critic_prompt": "Critique at ${draft_path}",
                    "rewrite_prompt": "Rewrite based on ${critique_path}",
                    "critic_disallowed_tools": ["yfinance"],
                    "rewrite_disallowed_tools": ["yfinance"],
                },
                "outputs": {"section": {"path": "artifacts/section.md", "format": "md"}},
            },
        },
    }
    dag = validate_dag(raw)
    assert dag.tasks["write"].config.n_iterations == 1


# ---------------------------------------------------------------------------
# Variable substitution tests (load_dag)
# ---------------------------------------------------------------------------

from schema import load_dag


def test_load_dag_substitutes_variables():
    raw = {
        "dag": {"version": 2, "name": "Test", "inputs": {"ticker": "${ticker}", "workdir": "${workdir}"}},
        "tasks": {
            "profile": {
                "description": "Get profile",
                "type": "python",
                "config": {"script": "skills/run.py", "args": {"ticker": "${ticker}", "workdir": "${workdir}"}},
                "outputs": {"profile": {"path": "artifacts/profile.json", "format": "json"}},
            },
        },
    }
    variables = {"ticker": "AAPL", "workdir": "work/AAPL_20260223"}
    dag = load_dag(raw, variables)
    task = dag.tasks["profile"]
    assert task.config.args["ticker"] == "AAPL"
    assert task.config.args["workdir"] == "work/AAPL_20260223"


def test_load_dag_substitutes_in_prompt():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "write": {
                "description": "Write",
                "type": "claude",
                "config": {
                    "prompt": "Analyze ${ticker} stock",
                },
            },
        },
    }
    variables = {"ticker": "MSFT"}
    dag = load_dag(raw, variables)
    assert dag.tasks["write"].config.prompt == "Analyze MSFT stock"


# ---------------------------------------------------------------------------
# Integration test: validate actual project DAG file
# ---------------------------------------------------------------------------

def test_sra_yaml_validates():
    """The actual project DAG file passes v2 validation."""
    from pathlib import Path
    import yaml

    yaml_path = Path(__file__).parent.parent / "dags" / "sra.yaml"
    with yaml_path.open() as f:
        raw = yaml.safe_load(f)
    dag = validate_dag(raw)
    assert dag.dag.version == 2
    assert len(dag.tasks) > 0
    # Verify all task types are valid
    for task_id, task in dag.tasks.items():
        assert task.type in ("python", "claude", "shell"), f"Bad type in {task_id}"


def test_sra_yaml_critic_config():
    """Write tasks in the actual DAG have valid critic-optimizer config."""
    from pathlib import Path
    import yaml

    yaml_path = Path(__file__).parent.parent / "dags" / "sra.yaml"
    with yaml_path.open() as f:
        raw = yaml.safe_load(f)
    dag = validate_dag(raw)

    write_tasks = [tid for tid in dag.tasks if tid.startswith("write_")]
    # Exclude write_conclusion and write_intro — they don't need critic loops
    section_writers = [
        tid for tid in write_tasks
        if tid not in ("write_conclusion", "write_intro")
    ]
    assert len(section_writers) == 7

    for tid in section_writers:
        task = dag.tasks[tid]
        assert task.config.n_iterations >= 1, f"{tid} should have n_iterations >= 1"
        assert task.config.critic_prompt, f"{tid} missing critic_prompt"
        assert task.config.rewrite_prompt, f"{tid} missing rewrite_prompt"
        assert "${draft_path}" in task.config.critic_prompt, f"{tid} critic_prompt missing ${{draft_path}}"
        assert "${critique_path}" in task.config.critic_prompt, f"{tid} critic_prompt missing ${{critique_path}}"
        assert "${draft_path}" in task.config.rewrite_prompt, f"{tid} rewrite_prompt missing ${{draft_path}}"
        assert "${rewrite_path}" in task.config.rewrite_prompt, f"{tid} rewrite_prompt missing ${{rewrite_path}}"


# ---------------------------------------------------------------------------
# Integration test: db.py init with v2 YAML
# ---------------------------------------------------------------------------

import subprocess


def test_db_init_with_v2_yaml(tmp_path):
    """db.py init successfully loads the v2 YAML and populates the database."""
    workdir = tmp_path / "test_run"
    result = subprocess.run(
        [
            "uv", "run", "python", "skills/db.py", "init",
            "--workdir", str(workdir),
            "--dag", "dags/sra.yaml",
            "--ticker", "TEST",
        ],
        capture_output=True,
        text=True,
        cwd="/Users/drucev/projects/sra5",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    import json
    output = json.loads(result.stdout)
    assert output["status"] == "ok"
    assert output["tasks"] > 0


# ---------------------------------------------------------------------------
# Integration tests: db.py validate command
# ---------------------------------------------------------------------------


def test_db_validate_command_valid():
    """db.py validate succeeds on valid v2 YAML."""
    result = subprocess.run(
        [
            "uv", "run", "python", "skills/db.py", "validate",
            "--dag", "dags/sra.yaml",
            "--ticker", "TEST",
        ],
        capture_output=True,
        text=True,
        cwd="/Users/drucev/projects/sra5",
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    import json
    output = json.loads(result.stdout)
    assert output["status"] == "ok"


def test_db_validate_command_invalid(tmp_path):
    """db.py validate fails on invalid YAML with clear error."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("""
dag:
  version: 2
  name: Bad
tasks:
  broken:
    description: Missing type
    config:
      command: echo hi
""")
    result = subprocess.run(
        [
            "uv", "run", "python", "skills/db.py", "validate",
            "--dag", str(bad_yaml),
            "--ticker", "TEST",
        ],
        capture_output=True,
        text=True,
        cwd="/Users/drucev/projects/sra5",
    )
    assert result.returncode == 1
