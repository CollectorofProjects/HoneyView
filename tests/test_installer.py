import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "honeyview.sh"


def test_installer_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


def test_installer_help_is_available_without_root():
    result = subprocess.run(
        [str(INSTALLER), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--accept-risk" in result.stdout
    assert "/opt/honeyview" in result.stdout
    assert "CollectorofProjects/HoneyView" in result.stdout


def test_installer_defaults_to_official_repository():
    content = INSTALLER.read_text()
    assert "https://github.com/CollectorofProjects/HoneyView.git" in content
