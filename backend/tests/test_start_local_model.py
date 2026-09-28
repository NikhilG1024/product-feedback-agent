"""Local model launcher guardrails without starting processes."""
import os
from pathlib import Path
import runpy

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "start-local-model"
launcher = runpy.run_path(str(SCRIPT), run_name="launcher_test")


def test_private_env_update_is_atomic_idempotent_and_keeps_unrelated_settings(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MONGODB_URI=mongodb://example.invalid\nSUMMARY_LLM_PROVIDER=groq\n")
    values = {"SUMMARY_LLM_PROVIDER": "local", "LOCAL_MODEL_API_KEY": "example-private-key",
              "LOCAL_MODEL_API_URL": "https://demo.ngrok-free.app/v1"}
    assert launcher["update_private_env"](env_file, values)
    text = env_file.read_text()
    assert "MONGODB_URI=mongodb://example.invalid" in text
    assert "SUMMARY_LLM_PROVIDER=local" in text
    assert "LOCAL_MODEL_API_KEY=example-private-key" in text
    assert env_file.stat().st_mode & 0o077 == 0
    assert not launcher["update_private_env"](env_file, values)


def test_load_config_rejects_insecure_key_file_without_exposing_key(tmp_path):
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"GGUF")
    key_file = tmp_path / "key"
    key_file.write_text("private-example-key")
    os.chmod(key_file, 0o644)
    env_file = tmp_path / ".env"
    env_file.write_text(f"LOCAL_MODEL_FILE={model_file}\nLOCAL_MODEL_API_KEY_FILE={key_file}\n")
    with pytest.raises(RuntimeError, match="owner-only") as error:
        launcher["load_config"](env_file)
    assert "private-example-key" not in str(error.value)


def test_matching_model_process_is_reused_and_unknown_port_owner_is_refused(monkeypatch, tmp_path):
    model_file = tmp_path / "model.gguf"
    key_file = tmp_path / "key"
    args = ["/opt/homebrew/bin/llama-server", "-m", str(model_file),
            *launcher["MODEL_ARGS"], "--api-key-file", str(key_file)]
    monkeypatch.setitem(launcher["model_process"].__globals__, "processes",
                        lambda: iter([(1234, args)]))
    monkeypatch.setitem(launcher["model_process"].__globals__, "listening",
                        lambda port: True)
    assert launcher["model_process"](model_file, key_file) == 1234
    monkeypatch.setitem(launcher["model_process"].__globals__, "processes",
                        lambda: iter([]))
    with pytest.raises(RuntimeError, match="unknown process"):
        launcher["model_process"](model_file, key_file)


def test_worker_discovery_is_scoped_to_this_repository(monkeypatch):
    fn = launcher['summary_worker_process']
    monkeypatch.setitem(fn.__globals__, 'processes', lambda: iter([
        (12, ['python', '-m', 'app.run_summary_worker']),
        (13, ['python', '-m', 'app.run_summary_worker'])]))
    monkeypatch.setitem(fn.__globals__, 'process_cwd',
        lambda pid: launcher['ROOT'] / 'backend' if pid == 12 else Path('/other/backend'))
    assert fn() == 12


def test_worker_start_loads_private_environment_without_command_line_secrets(monkeypatch):
    calls = []
    fn = launcher['start_summary_worker']
    monkeypatch.setitem(fn.__globals__, 'launch', lambda args, log, **kwargs: calls.append((args, kwargs)) or 'child')
    assert fn({'MONGODB_URI': 'private-uri', 'LOCAL_MODEL_API_URL': 'https://model.ngrok.app/v1'}) == 'child'
    args, options = calls[0]
    assert args[-2:] == ['-m', 'app.run_summary_worker']
    assert 'private-uri' not in ' '.join(args)
    assert options['env']['MONGODB_URI'] == 'private-uri'
    assert options['cwd'] == launcher['ROOT'] / 'backend'
