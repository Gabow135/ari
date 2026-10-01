from ari.infrastructure.command.shell_runner import ShellRunner


async def test_runs_echo_and_captures_stdout(tmp_path):
    result = await ShellRunner()("echo hola-ari", str(tmp_path))
    assert result.returncode == 0
    assert "hola-ari" in result.stdout
    assert result.timed_out is False


async def test_nonzero_exit_code(tmp_path):
    result = await ShellRunner()("exit 3", str(tmp_path))
    assert result.returncode == 3


async def test_runs_in_given_cwd(tmp_path):
    result = await ShellRunner()("pwd", str(tmp_path))
    assert str(tmp_path) in result.stdout


async def test_timeout_kills_and_flags(tmp_path):
    result = await ShellRunner(timeout=0.2)("sleep 5", str(tmp_path))
    assert result.timed_out is True
