"""Tests for DAG schema v2 Pydantic models."""

import pytest
from schema import OutputDef, DagHeader
from schema import PythonConfig, ClaudeConfig, ShellConfig, PerplexityConfig, OpenAIConfig
from schema import DagFile


def test_output_def_valid():
    out = OutputDef(path="artifacts/profile.json", format="json")
    assert out.path == "artifacts/profile.json"
    assert out.format == "json"


def test_output_def_missing_path():
    with pytest.raises(Exception):
        OutputDef(format="json")


def test_output_def_missing_format():
    with pytest.raises(Exception):
        OutputDef(path="artifacts/profile.json")


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


def test_python_config_valid():
    cfg = PythonConfig(script="skills/research_profile.py", args={"ticker": "AAPL"})
    assert cfg.script == "skills/research_profile.py"
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
        max_turns=10,
        tools=["read", "write"],
        reads_from=["profile", "technical"],
    )
    assert cfg.prompt == "Write a report about ${ticker}"
    assert cfg.tools == ["read", "write"]
    assert cfg.reads_from == ["profile", "technical"]


def test_claude_config_minimal():
    cfg = ClaudeConfig(prompt="Do something")
    assert cfg.system is None
    assert cfg.model is None
    assert cfg.max_turns is None
    assert cfg.tools == []
    assert cfg.reads_from == []


def test_claude_config_missing_prompt():
    with pytest.raises(Exception):
        ClaudeConfig(model="claude-sonnet-4-6")


def test_shell_config_valid():
    cfg = ShellConfig(command="pandoc input.md -o output.pdf")
    assert cfg.command == "pandoc input.md -o output.pdf"


def test_shell_config_missing_command():
    with pytest.raises(Exception):
        ShellConfig()


def test_perplexity_config_valid():
    cfg = PerplexityConfig(prompt="Research news about AAPL", model="sonar-pro")
    assert cfg.model == "sonar-pro"


def test_perplexity_config_defaults():
    cfg = PerplexityConfig(prompt="Research news")
    assert cfg.model is None
    assert cfg.reads_from == []


def test_openai_config_valid():
    cfg = OpenAIConfig(prompt="Summarize this", model="gpt-4o")
    assert cfg.model == "gpt-4o"


def test_task_python():
    from pydantic import TypeAdapter
    from schema import Task
    adapter = TypeAdapter(Task)
    task = adapter.validate_python({
        "description": "Get profile",
        "type": "python",
        "config": {"script": "skills/research_profile.py", "args": {"ticker": "AAPL"}},
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
        "config": {"prompt": "Write a report", "tools": ["read"]},
        "outputs": {"section": {"path": "artifacts/section.md", "format": "md"}},
    })
    assert task.type == "claude"
    assert isinstance(task.config, ClaudeConfig)
    assert task.depends_on == ["profile"]


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


def test_validate_dag_bad_reads_from_ref():
    raw = {
        "dag": {"version": 2, "name": "Test"},
        "tasks": {
            "a": {"description": "A", "type": "python", "config": {"script": "run.py"}},
            "b": {
                "description": "B",
                "type": "claude",
                "config": {"prompt": "do it", "reads_from": ["nonexistent"]},
            },
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
                "config": {"prompt": "Analyze ${ticker} stock"},
            },
        },
    }
    variables = {"ticker": "MSFT"}
    dag = load_dag(raw, variables)
    assert dag.tasks["write"].config.prompt == "Analyze MSFT stock"
