"""Tests for DAG schema v2 Pydantic models: OutputDef, SetsVarDef, DagHeader, configs, Task."""

import pytest
from pydantic import ValidationError
from schema import OutputDef, SetsVarDef, DagHeader
from schema import PythonConfig, ClaudeConfig, ShellConfig


# ---------------------------------------------------------------------------
# OutputDef
# ---------------------------------------------------------------------------


def test_output_def_valid():
    out = OutputDef(path="artifacts/profile.json", format="json")
    assert out.path == "artifacts/profile.json"
    assert out.format == "json"
    assert out.description == ""


def test_output_def_with_description():
    out = OutputDef(path="artifacts/profile.json", format="json", description="Company identity and valuation snapshot")
    assert out.description == "Company identity and valuation snapshot"


def test_output_def_missing_path():
    with pytest.raises(ValidationError):
        OutputDef(format="json")


def test_output_def_missing_format():
    with pytest.raises(ValidationError):
        OutputDef(path="artifacts/profile.json")


# ---------------------------------------------------------------------------
# SetsVarDef
# ---------------------------------------------------------------------------


def test_sets_var_def_valid():
    sv = SetsVarDef(artifact="artifacts/profile.json", key="company_name")
    assert sv.artifact == "artifacts/profile.json"
    assert sv.key == "company_name"


def test_sets_var_def_missing_artifact():
    with pytest.raises(ValidationError):
        SetsVarDef(key="company_name")


def test_sets_var_def_missing_key():
    with pytest.raises(ValidationError):
        SetsVarDef(artifact="artifacts/profile.json")


# ---------------------------------------------------------------------------
# DagHeader
# ---------------------------------------------------------------------------


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
    with pytest.raises(ValidationError):
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


# ---------------------------------------------------------------------------
# PythonConfig
# ---------------------------------------------------------------------------


def test_python_config_valid():
    cfg = PythonConfig(script="skills/fetch_profile/fetch_profile.py", args={"ticker": "AAPL"})
    assert cfg.script == "skills/fetch_profile/fetch_profile.py"
    assert cfg.args == {"ticker": "AAPL"}


def test_python_config_missing_script():
    with pytest.raises(ValidationError):
        PythonConfig(args={"ticker": "AAPL"})


def test_python_config_no_args():
    cfg = PythonConfig(script="skills/run.py")
    assert cfg.args == {}


# ---------------------------------------------------------------------------
# ClaudeConfig
# ---------------------------------------------------------------------------


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
    with pytest.raises(ValidationError):
        ClaudeConfig(prompt="Do it", permission_mode="invalid")


def test_claude_config_invalid_output_format():
    with pytest.raises(ValidationError):
        ClaudeConfig(prompt="Do it", output_format="xml")


def test_claude_config_invalid_effort():
    with pytest.raises(ValidationError):
        ClaudeConfig(prompt="Do it", effort="ultra")


def test_claude_config_missing_prompt():
    with pytest.raises(ValidationError):
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


# ---------------------------------------------------------------------------
# ShellConfig
# ---------------------------------------------------------------------------


def test_shell_config_valid():
    cfg = ShellConfig(command="pandoc input.md -o output.pdf")
    assert cfg.command == "pandoc input.md -o output.pdf"


def test_shell_config_missing_command():
    with pytest.raises(ValidationError):
        ShellConfig()


# ---------------------------------------------------------------------------
# Task (discriminated union)
# ---------------------------------------------------------------------------


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
    with pytest.raises(ValidationError):
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
    with pytest.raises(ValidationError):
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


# ---------------------------------------------------------------------------
# DagFile
# ---------------------------------------------------------------------------


def test_dagfile_valid():
    from schema import DagFile
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
    from schema import DagFile
    with pytest.raises(ValidationError):
        DagFile(
            dag={"version": 1, "name": "Old"},
            tasks={},
        )
