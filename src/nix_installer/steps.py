import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, override

from nix_installer.installer import (
    InstallStep,
    InstallStepError,
)

if TYPE_CHECKING:
    from nix_installer.config import (
        FetchSSHKeyPairConfig,
        RefreshDepsAction,
        UseDiskEncryptionAction,
    )

_FACTER_PATH: str = "/tmp/facter.json"
_CACHE_PATH: str = ".cache/nix_installer"


class ConfigureSubstitutersStepError(InstallStepError): ...


class ConfigureSubstitutersStep(InstallStep):
    name = "config_substituters"
    description = "Configure Nix substituters on target"
    error_cls = ConfigureSubstitutersStepError

    @override
    def execute(self) -> None:
        substituters = self.get("nix.settings.substituters")
        trusted_keys = self.get("nix.settings.trusted-public-keys")
        nix_conf_data = (
            f"extra-substituters = {substituters}\n"
            f"extra-trusted-public-keys = {trusted_keys}\n"
        )
        self.run_cmd(
            "Writing substituter config to target",
            cmd=self.mk_sh(
                "mkdir -p ~/.config/nix && printf '%s' "
                f"'{nix_conf_data}' >> ~/.config/nix/nix.conf"
            ),
            ssh=True,
        )


class SendKeyFileStepError(InstallStepError): ...


class SendKeyFileStep(InstallStep):
    name = "send_key_file"
    description = "Decrypt and send keyfile for disk encryption"
    error_cls = SendKeyFileStepError

    @property
    def _cfg(self) -> UseDiskEncryptionAction | None:
        return self.ctx.config.actions.use_disk_encryption

    @override
    def should_skip(self) -> bool:
        return self._cfg is None

    @override
    def execute(self) -> None:
        if self._cfg is None:
            msg = "Action data is unexpectedly missing"
            raise self.mk_error(msg)
        sops_key = self._cfg.sops_key or self.ctx.nixos_host
        if isinstance(sops_key, str):
            sops_key = [sops_key]
        # we have to turn the input key, a list of strings, into a
        # SOPS-compatible yaml selector, which takes a form like:
        # ["a"]["b"]["c"] (from the list ["a", "b", "c"])
        built_sops_key = "".join(f'["{part}"]' for part in sops_key)
        sops_path = Path(self.ctx.data_dir).expanduser() / self._cfg.sops_file
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            tmp_target_file = tmp_dir / "secret.key"
            self.run_cmd(
                "Decrypting disk encryption keyfile with SOPS",
                [
                    "sops",
                    "-d",
                    "--extract",
                    built_sops_key,
                    "--output",
                    str(tmp_target_file.resolve()),
                    str(sops_path),
                ],
                env={
                    "SOPS_AGE_KEY_FILE": str(
                        self._cfg.sops_age_keyfile.expanduser().resolve()
                    ),
                }
                if self._cfg.sops_age_keyfile
                else {},
            )
            self.upload_file(
                source_path=tmp_target_file,
                target_path=self._cfg.sent_keyfile_location,
            )


class RunDiskoStepError(InstallStepError): ...


class RunDiskoStep(InstallStep):
    name = "run_disko"
    description = "Build and run disko on target"
    error_cls = RunDiskoStepError

    @override
    def execute(self) -> None:
        self.cli.info("Building disko script...")
        disko_script_path = self.build("system.build.diskoScript")
        self.cli.info(f"Built disko script: {disko_script_path}")
        self.run_cmd(
            "Copying disko script to target",
            self.mk_nix_copy(disko_script_path),
        )
        self.run_cmd(
            "Running disko",
            [str(disko_script_path)],
            ssh=True,
            sudo=True,
            use_render_str="disko",
        )
        self.collect_garbage()


class RunFacterStepError(InstallStepError): ...


class RunFacterStep(InstallStep):
    name = "run_facter"
    description = "Build and run facter on target"
    error_cls = RunFacterStepError

    @override
    def should_skip(self) -> bool:
        action = self.ctx.config.actions.fetch_host_data
        return action is None or action.facter is None

    @override
    def execute(self) -> None:
        self.cli.info("Building nixos-facter...")
        facter_path = self.build(
            "pkgs.nixos-facter",
            prepend_config=False,
        )
        self.cli.info(f"Built nixos-facter: {facter_path}")
        self.run_cmd(
            "Copying facter script to target",
            self.mk_nix_copy(facter_path),
        )
        self.run_cmd(
            "Running facter",
            [f"{facter_path}/bin/nixos-facter", "-o", _FACTER_PATH],
            ssh=True,
            sudo=True,
            use_render_str="nixos-facter",
        )
        self.collect_garbage()


class DownloadFacterStepError(InstallStepError): ...


class DownloadFacterStep(InstallStep):
    name = "download_facter"
    description = "Download facter report from target"
    error_cls = DownloadFacterStepError

    @override
    def should_skip(self) -> bool:
        action = self.ctx.config.actions.fetch_host_data
        return action is None or action.facter is None

    @override
    def execute(self) -> None:
        # WARN - this will only work with local dirs, needs refactor for
        # remote checkouts
        action = self.ctx.config.actions.fetch_host_data
        if not action:
            msg = "Action data unexpectedly missing."
            raise self.mk_error(msg)
        if not action.facter:
            msg = "Action facter data unexpectedly missing."
            raise self.mk_error(msg)
        facter_save_dir = (
            Path(self.ctx.data_dir).expanduser() / action.facter.save_folder
        )
        facter_save_dir.mkdir(parents=True, exist_ok=True)
        destination_file = facter_save_dir / f"{self.ctx.nixos_host}.json"
        self.download_file(
            remote_path=_FACTER_PATH,
            destination_path=destination_file,
        )


class GenerateSSHKeysStepError(InstallStepError): ...


class GenerateSSHKeysStep(InstallStep):
    name = "generate_ssh_keys"
    description = "Generate SSH keypair"
    error_cls = GenerateSSHKeysStepError

    @property
    def _cfg(self) -> FetchSSHKeyPairConfig | None:
        action = self.ctx.config.actions.fetch_host_data
        if not action:
            return None
        return action.ssh_keypair

    @override
    def should_skip(self) -> bool:
        return self._cfg is None

    @override
    def execute(self) -> None:
        if self._cfg is None:
            msg = "Action data unexpectedly missing"
            raise self.mk_error(msg)
        target_key_dir = f"/mnt/{self._cfg.target_copy_folder.strip('/')}"
        key_path = f"{target_key_dir}/{self._cfg.keyfile_name}"
        key_path_pub = f"{key_path}.pub"
        self.run_cmd(
            "Creating target SSH key directory",
            ["mkdir", "-p", target_key_dir],
            ssh=True,
            sudo=True,
        )
        self.run_cmd(
            "Generating host SSH keypair",
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-a",
                "32",
                "-N",
                "",
                "-f",
                key_path,
            ],
            ssh=True,
            sudo=True,
        )
        download_save_path = (
            Path(self.ctx.data_dir).expanduser()
            / self._cfg.save_folder
            / self.ctx.nixos_host
        )
        download_save_path.parent.mkdir(parents=True, exist_ok=True)
        self.download_file(
            remote_path=key_path_pub,
            destination_path=download_save_path,
        )
        if self._cfg.target_copy_to_user:
            target_user = self.get_user()
            if target_user is None:
                msg = "Cannot copy SSH keypair without a target user"
                raise self.mk_error(msg)
            target_user_key_dir = f"/mnt/home/{target_user}/.ssh"
            user_key_path = f"{target_user_key_dir}/{self._cfg.keyfile_name}"
            user_key_pub_path = f"{user_key_path}.pub"
            self.run_cmd(
                "Creating target user's SSH key directory",
                ["install", "-d", "-m", "700", target_user_key_dir],
                ssh=True,
                sudo=True,
            )
            self.run_cmd(
                "Copying keypair to target user's home",
                self.mk_sh(
                    f"install -m 600 {shlex.quote(key_path)} "
                    f"{shlex.quote(user_key_path)} && "
                    f"install -m 644 {shlex.quote(key_path_pub)} "
                    f"{shlex.quote(user_key_pub_path)}"
                ),
                ssh=True,
                sudo=True,
            )


class CommitAndPushStepError(InstallStepError): ...


class CommitAndPushStep(InstallStep):
    name = "commit_and_push"
    description = "Commit and push stuff on 'data' source."
    error_cls = CommitAndPushStepError

    @override
    def should_skip(self) -> bool:
        if self.ctx.git_cfg.push and not self.ctx.git_cfg.commit:
            msg = "Cannot have push set to true and commit set to false"
            raise self.mk_error(msg)

        return not self.ctx.git_cfg.commit

    @override
    def execute(self) -> None:
        self.run_cmd(
            "Staging changed files...",
            self.mk_git_data.add(["."]),
        )
        self.run_cmd(
            "Commiting changed files...",
            self.mk_git_data.commit(self.commit_msg),
            ok_codes=(0, 1),  # avoid erroring if nothing to commit
        )
        if self.ctx.git_cfg.push:
            self.run_cmd(
                "Pushing new commits",
                self.mk_git_data.push(),
            )


class RefreshDepsStepError(InstallStepError): ...


class RefreshDepsStep(InstallStep):
    name = "refresh_deps"
    description = "Refresh dependencies in install repo"
    error_cls = RefreshDepsStepError

    @property
    def _cfg(self) -> RefreshDepsAction | None:
        return self.ctx.config.actions.refresh_deps

    @override
    def should_skip(self) -> bool:
        if self.ctx.git_cfg.push and not self.ctx.git_cfg.commit:
            msg = "Cannot have push set to true and commit set to false"
            raise self.mk_error(msg)
        return self._cfg is None

    @override
    def execute(self) -> None:
        # WARN - this will only work with local dirs, needs refactor for
        # remote checkouts
        if self._cfg is None:
            msg = "Config for refresh_deps action cannot be None"
            raise self.mk_error(msg)
        self.run_cmd(
            "Updating/refreshing deps",
            self.mk_sh(
                f"cd {shlex.quote(self.ctx.install_dir)} && "
                f"{self._cfg.command}"
            ),
        )
        if not self.ctx.git_cfg.commit:
            return
        self.run_cmd(
            "Staging changed files...",
            self.mk_git_install.add(["."]),
        )
        self.run_cmd(
            "Commiting changed files...",
            self.mk_git_install.commit(self.commit_msg),
            ok_codes=(0, 1),  # avoid erroring if nothing to commit
        )
        if self.ctx.git_cfg.push:
            self.run_cmd(
                "Pushing new commits",
                self.mk_git_install.push(),
            )


type ChownOperation = tuple[str, str, str | None, bool]


class PostInstallChownStepError(InstallStepError): ...


class PostInstallChownStep(InstallStep):
    name = "post_install_chown"
    description = "Fix installed-system file ownership"
    error_cls = PostInstallChownStepError

    @property
    def _ssh_key_cfg(self) -> FetchSSHKeyPairConfig | None:
        action = self.ctx.config.actions.fetch_host_data
        if not action:
            return None
        return action.ssh_keypair

    def _should_fix_ssh_key_ownership(self) -> bool:
        return (
            self._ssh_key_cfg is not None
            and self._ssh_key_cfg.target_copy_to_user
        )

    @override
    def should_skip(self) -> bool:
        return (
            not self.ctx.params.ssh.sudo
            and not self._should_fix_ssh_key_ownership()
        )

    def _ssh_key_operations(self) -> list[ChownOperation]:
        cfg = self._ssh_key_cfg
        if cfg is None or not cfg.target_copy_to_user:
            return []
        target_user = self.get_user()
        if target_user is None:
            msg = "Cannot fix SSH key ownership without a target user"
            raise self.mk_error(msg)
        return [(target_user, f"/home/{target_user}/.ssh", None, True)]

    def _operations(self) -> list[ChownOperation]:
        return self._ssh_key_operations()

    def _owner_spec(self, owner: str, group: str | None) -> str:
        if group is None:
            return f"{owner}:"
        return f"{owner}:{group}"

    def _restore_nix_ownership(self) -> None:
        self.run_cmd(
            "Restoring installed /nix ownership",
            [
                "chroot",
                "/mnt",
                "/nix/var/nix/profiles/system/sw/bin/chown",
                "-R",
                "root:root",
                "/nix",
            ],
            ssh=True,
            sudo=True,
            use_render_str="chroot chown",
        )
        self.run_cmd(
            "Restoring installed /nix/store permissions",
            [
                "chroot",
                "/mnt",
                "/nix/var/nix/profiles/system/sw/bin/chmod",
                "1775",
                "/nix/store",
            ],
            ssh=True,
            sudo=True,
            use_render_str="chroot chmod",
        )
        self.run_cmd(
            "Restoring installed /nix/store group",
            [
                "chroot",
                "/mnt",
                "/nix/var/nix/profiles/system/sw/bin/chown",
                "root:nixbld",
                "/nix/store",
            ],
            ssh=True,
            sudo=True,
            use_render_str="chroot chown",
        )

    @override
    def execute(self) -> None:
        if self.ctx.params.ssh.sudo:
            self._restore_nix_ownership()
        for owner, path, group, recursive in self._operations():
            self.run_cmd(
                f"Fixing installed ownership for {path}",
                [
                    "chroot",
                    "/mnt",
                    "/nix/var/nix/profiles/system/sw/bin/chown",
                    *(["-R"] if recursive else []),
                    self._owner_spec(owner, group),
                    path,
                ],
                ssh=True,
                sudo=True,
                use_render_str="chroot chown",
            )


class NixOSInstallStepError(InstallStepError): ...


class NixOSInstallStep(InstallStep):
    name = "nixos_install"
    description = "Install NixOS closure"
    error_cls = NixOSInstallStepError

    def _grant_nix_copy_access(self) -> None:
        self.run_cmd(
            "Granting target SSH user write access to /mnt/nix",
            self.mk_sh("mkdir -p /mnt/nix && chown -R $SUDO_USER /mnt/nix"),
            ssh=True,
            sudo=True,
        )


    @override
    def execute(self) -> None:
        if self.ctx.params.ssh.sudo:
            self._grant_nix_copy_access()
        self.cli.info("Building NixOS system closure...")
        store_path = self.build("system.build.toplevel")
        self.cli.info(f"Built NixOS system closure: {store_path}")
        self.run_cmd(
            "Copying system closure to target /mnt store",
            self.mk_nix_copy(
                store_path=store_path,
                remote_store="local?root=/mnt",
                substitute_on_dest=True,
            ),
        )
        self.run_cmd(
            f"Installing NixOS for {self.ctx.nixos_host}",
            [
                "nixos-install",
                "--no-root-password",
                "--no-channel-copy",
                "--root",
                "/mnt",
                "--system",
                str(store_path),
            ],
            ssh=True,
            sudo=True,
        )
