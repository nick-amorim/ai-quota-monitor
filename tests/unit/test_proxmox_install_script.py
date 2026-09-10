from __future__ import annotations

from pathlib import Path


SCRIPT = Path("scripts/proxmox/install-lxc.sh")


def test_restart_helper_install_creates_sudoers_directory_and_sudo_dependency():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "ensure_restart_helper_dependencies()" in content
    assert "apt-get install -y sudo" in content
    assert 'mkdir -p "$(dirname "$RESTART_HELPER")" "$(dirname "$SUDOERS_FILE")"' in content
    assert "install_restart_helper() {\n  ensure_restart_helper_dependencies" in content
