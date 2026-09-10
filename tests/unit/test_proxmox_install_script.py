from __future__ import annotations

from pathlib import Path


SCRIPT = Path("scripts/proxmox/install-lxc.sh")


def test_restart_helper_install_creates_sudoers_directory_and_sudo_dependency():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "ensure_restart_helper_dependencies()" in content
    assert "apt-get install -y sudo" in content
    assert 'mkdir -p "$(dirname "$RESTART_HELPER")" "$(dirname "$SUDOERS_FILE")"' in content
    assert "install_restart_helper() {\n  ensure_restart_helper_dependencies" in content


def test_update_mode_bootstraps_checkout_with_tracked_only_status():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "--repair-checkout" in content
    assert "bootstrap_checkout_update()" in content
    assert "git -C \"$INSTALL_DIR\" status --porcelain --untracked-files=no" in content
    assert "git -C \"$INSTALL_DIR\" diff --binary HEAD > \"$patch_file\"" in content
    assert "git -C \"$INSTALL_DIR\" restore --source=HEAD --staged --worktree ." in content
    assert "--update --repair-checkout" in content
    assert "git -C \"$INSTALL_DIR\" merge --ff-only \"origin/${BRANCH}\"" in content
    assert "repair_install_ownership\n  bootstrap_checkout_update\n  \"$UPDATE_WRAPPER\"" in content
