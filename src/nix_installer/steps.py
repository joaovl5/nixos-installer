import shlex
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, override

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


class ConfigureSubstitutersStep(
    InstallStep[ConfigureSubstitutersStepError],
):
    name: ClassVar[str] = "config_substituters"
    description: ClassVar[str] = "Configure Nix substituters on target"

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


class SendKeyFileStep(InstallStep[SendKeyFileStepError]):
    name: ClassVar[str] = "send_key_file"
    description: ClassVar[str] = (
        "Decrypt and send keyfile for disk encryption"
    )

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
                ],
                env={
                    "SOPS_AGE_KEY_FILE": str(
                        self._cfg.sops_age_keyfile.resolve()
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


class RunDiskoStep(InstallStep[RunDiskoStepError]):
    name: ClassVar[str] = "run_disko"
    description: ClassVar[str] = "Build and run disko on target"

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


class RunFacterStep(InstallStep[RunFacterStepError]):
    name: ClassVar[str] = "run_facter"
    description: ClassVar[str] = "Build and run facter on target"

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
            [str(facter_path)],
            ssh=True,
            sudo=True,
            use_render_str="disko",
        )
        self.collect_garbage()


class DownloadFacterStepError(InstallStepError): ...


class DownloadFacterStep(InstallStep[DownloadFacterStepError]):
    name: ClassVar[str] = "run_facter"
    description: ClassVar[str] = "Build and run facter on target"

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
        save_dir = self.ctx.config.data.uri.uri.render()
        facter_dir_suffix = action.facter.save_folder
        facter_save_dir = save_dir / facter_dir_suffix
        facter_save_dir.mkdir(parents=True, exist_ok=True)
        destination_file = facter_save_dir / f"{self.ctx.nixos_host}.json"
        self.download_file(
            remote_path=_FACTER_PATH,
            destination_path=destination_file,
        )


class GenerateSSHKeysStepError(InstallStepError): ...


class GenerateSSHKeysStep(InstallStep[GenerateSSHKeysStepError]):
    name: ClassVar[str] = "generate_ssh_keys"
    description: ClassVar[str] = "Generate SSH keypair"

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
        target_key_dir = f"/mnt/{self._cfg.target_copy_folder}"
        key_path = f"{target_key_dir}/{self._cfg.keyfile_name}"
        key_path_pub = f"{key_path}.pub"
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
            Path(self.ctx.data_dir)
            / self._cfg.save_folder
            / self.ctx.nixos_host
        )
        self.download_file(
            remote_path=key_path_pub,
            destination_path=download_save_path,
        )
        if self._cfg.target_copy_to_user:
            target_user = self.get_user()
            target_user_key_dir = f"/mnt/home/{target_user}/.ssh"
            user_key_path = f"{target_user_key_dir}/{self._cfg.keyfile_name}"
            user_key_pub_path = f"{user_key_path}.pub"
            self.run_cmd(
                "Copying keypair to target user's home",
                self.mk_sh(
                    f"install -m 600 {key_path} {user_key_path} && "
                    f"install -m 644 {key_path_pub} {user_key_pub_path} && "
                ),
            )


class CommitAndPushStepError(InstallStepError): ...


class CommitAndPushStep(InstallStep[CommitAndPushStepError]):
    name: ClassVar[str] = "commit_and_push"
    description: ClassVar[str] = "Commit and push stuff on 'data' source."

    @override
    def should_skip(self) -> bool:
        if self.ctx.git_cfg.push and not self.ctx.git_cfg.commit_msg:
            msg = "Cannot have push set to true and commit not set to true as well"
            raise self.mk_error(msg)

        return not self.ctx.git_cfg.commit or self.ctx.git_cfg.push

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


class RefreshDepsStep(InstallStep[RefreshDepsStepError]):
    name: ClassVar[str] = "refresh_deps"
    description: ClassVar[str] = "Refresh dependencies in install repo"

    @property
    def _cfg(self) -> RefreshDepsAction | None:
        return self.ctx.config.actions.refresh_deps

    @override
    def should_skip(self) -> bool:
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


class NixOSInstallStepError(InstallStepError): ...


class NixOSInstallStep(InstallStep[NixOSInstallStepError]):
    name: ClassVar[str] = "nixos_install"
    description: ClassVar[str] = "Install NixOS closure"

    @override
    def execute(self) -> None:
        self.run_cmd(
            "Granting target SSH user write access to /mnt/nix",
            self.mk_sh("mkdir -p /mnt/nix && chown -R $SUDO_USER /mnt/nix"),
        )
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
