from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path
from textwrap import dedent
from time import time
from typing import TYPE_CHECKING, ClassVar, TypeVar, cast
from urllib.parse import quote
from uuid import uuid4

from attrs import define
from coisas.commands.git import GitCommandFactory
from coisas.commands.nix import NixCommand
from coisas.commands.rsync import RsyncCommand
from coisas.commands.shell import ShellCommand, ShellCommandProgram
from coisas.commands.ssh import SSHCommandWrapper, SSHConfig
from coisas.commands.sudo import SudoCommandWrapper
from coisas.commands.wrappers.passthrough import PassthroughCommandWrapper

from nix_installer.evals import NixRef

if TYPE_CHECKING:
    from collections.abc import Sequence

    from coisas.cli.cli import CLIPanelFactory
    from coisas.commands.wrappers.base import BaseCommandWrapper

    from nix_installer.config import AppConfig, GitConfig
    from nix_installer.params import AppParams

_CACHE_PATH: str = ".cache/nix_installer"


@define(kw_only=True)
class InstallerContext:
    params: AppParams
    config: AppConfig

    @property
    def ssh_config(self) -> SSHConfig:
        """SSH config for commands."""
        return SSHConfig(
            host=self.params.ssh.host,
            port=self.params.ssh.port,
            identity_file=self.params.ssh.identity,
        )

    @property
    def git_cfg(self) -> GitConfig:
        """GitConfig shortcut."""
        return self.config.git

    @property
    def data_dir(self) -> str:
        """Shortcut for data dir."""
        return self.config.data.uri.uri.render()

    @property
    def install_dir(self) -> str:
        """Shortcut for install repo dir."""
        return self.config.install.uri.uri.render()

    @property
    def nixos_host(self) -> str:
        """Shortcut for current install host config name."""
        return self.params.nixos.host

    @property
    def do_ssh(self) -> SSHCommandWrapper:
        """SSH wrapper for commands."""
        return SSHCommandWrapper(config=self.ssh_config)

    @property
    def do_sudo(self) -> SudoCommandWrapper | PassthroughCommandWrapper:
        """Make command wrapper for sudo only if context params require it."""
        return (
            SudoCommandWrapper()
            if self.params.ssh.sudo
            else PassthroughCommandWrapper()
        )


class InstallStepError(RuntimeError): ...


class InstallStep(ABC):
    name: ClassVar[str]
    """Step name."""

    description: ClassVar[str]
    """Step description."""

    error_cls: type[InstallStepError]

    ctx: InstallerContext
    cli: CLIPanelFactory

    def __init__(
        self,
        ctx: InstallerContext,
        cli: CLIPanelFactory,
    ) -> None:
        """Instantiate an InstallStep."""
        self.ctx = ctx
        self.cli = cli

    def _create_git_fac(self, repo_path: str) -> GitCommandFactory:
        return GitCommandFactory(
            config={
                "user.name": self.ctx.git_cfg.commit_user_name,
                "user.email": self.ctx.git_cfg.commit_user_email,
            },
            repo_path=repo_path,
        )

    @cached_property
    def commit_msg(self) -> str:
        """Rendered commit message from context."""
        return self.ctx.git_cfg.commit_msg.replace(
            "$HOST",
            self.ctx.nixos_host,
        )

    @cached_property
    def mk_git_data(self) -> GitCommandFactory:
        """Baked GitCommandFactory from contextual data."""
        return self._create_git_fac(self.ctx.data_dir)

    @cached_property
    def mk_git_install(self) -> GitCommandFactory:
        """Baked GitCommandFactory from contextual data."""
        return self._create_git_fac(self.ctx.install_dir)

    def should_skip(self) -> bool:
        """Whether step should be skipped given context."""
        return False

    @abstractmethod
    def execute(self) -> None:
        """Execute step effects."""

    def _mk_nix_ref(
        self,
        attr_path: str,
        *,
        prepend_config: bool = True,
    ) -> NixRef.Type:
        if prepend_config:
            attr_path = f"config.{attr_path}"
        install_dir = Path(self.ctx.install_dir).expanduser().resolve()
        return NixRef.File(
            file=install_dir,
            attr_path=f"nixosConfigurations.{self.ctx.nixos_host}.{attr_path}",
        )

    def get(
        self,
        attr_path: str,
        *,
        prepend_config: bool = True,
    ) -> str:
        """Get string through a nix eval."""
        return self._mk_nix_ref(
            attr_path=attr_path,
            prepend_config=prepend_config,
        ).run_eval_as_str(
            cli=self.cli,
            extra_args=self.ctx.config.nix.extra_args,
            env=self.ctx.config.nix.extra_env_vars,
        )

    def build(
        self,
        attr_path: str,
        *,
        prepend_config: bool = True,
    ) -> Path:
        """Build derivation through a nix build."""
        return self._mk_nix_ref(
            attr_path=attr_path,
            prepend_config=prepend_config,
        ).build_drv(
            cli=self.cli,
            extra_args=self.ctx.config.nix.extra_args,
            env=self.ctx.config.nix.extra_env_vars,
        )

    def mk_sh(self, cmd: str) -> ShellCommand:
        """Make a shell command to be run in POSIX 'sh'."""
        return ShellCommand(
            program=ShellCommandProgram(shell_args=["sh", "-c"]),
            args=[cmd],
        )

    def mk_nix_copy(
        self,
        store_path: Path,
        remote_store: str | None = None,
        *,
        substitute_on_dest: bool = False,
    ) -> NixCommand:
        """Generate command for copying a derivation into a remote store."""
        ssh_cfg = self.ctx.ssh_config
        dest = f"ssh://{ssh_cfg.host}"
        if remote_store:
            dest += f"?remote-store={quote(remote_store, safe='')}"
        return NixCommand(
            args=[
                "copy",
                "--to",
                dest,
                *(
                    ["--substitute-on-destination"]
                    if substitute_on_dest
                    else []
                ),
                str(store_path),
            ],
            env={
                "NIX_SSHOPTS": f"-i {ssh_cfg.identity_file} -p {ssh_cfg.port}"
            },
        )

    def collect_garbage(self) -> None:
        """Run nix-collect-garbage on target."""
        self.run_cmd(
            "Collecting nix store garbage",
            ["nix-collect-garbage", "-d"],
            ssh=True,
            sudo=True,
        )

    def mk_rsync_cmd(
        self,
        src: str,
        dest: str,
        *,
        remote_dest: bool = True,
    ) -> RsyncCommand:
        """Make RsyncCommand instance."""
        return RsyncCommand(
            src=src,
            dest=dest,
            ssh_config=self.ctx.ssh_config,
            remote_dest=remote_dest,
        )

    def get_user(self) -> str | None:
        """Attempt to get username for the config."""
        user_list_raw = self._mk_nix_ref("users.users").run_eval_json(
            cli=self.cli,
            apply=dedent(
                """
                user_list:
                    builtins.filter
                    (name: user_list.${name}.isNormalUser or false)
                    (builtins.attrNames user_list)
                """
            ).strip(),
            extra_args=self.ctx.config.nix.extra_args,
            env=self.ctx.config.nix.extra_env_vars,
        )
        if not isinstance(user_list_raw, list):
            msg = "Unexpected user list from Nix config"
            raise self.mk_error(msg)
        user_list_unknown = cast("list[object]", user_list_raw)
        if not all(isinstance(user, str) for user in user_list_unknown):
            msg = "Unexpected user list from Nix config"
            raise self.mk_error(msg)
        user_list = cast("list[str]", user_list_unknown)
        match len(user_list):
            case 0:
                msg = "Found no users for config"
                raise self.mk_error(msg)
            case 1:
                return user_list[0]
            case _:
                msg = "Couldn't discern what user to get"
                raise self.mk_error(msg)

    def _ensure_cached_dir(self) -> None:
        self.run_cmd(
            "Ensuring remote cache dir exists",
            ["mkdir", "-p", _CACHE_PATH],
            ssh=True,
        )

    def upload_file(self, source_path: Path, target_path: str) -> None:
        """Upload a file into host using an intermediary cached dir for handling perms."""
        self._ensure_cached_dir()
        intermediary_file = f"{_CACHE_PATH}/{uuid4().hex}"
        self.run_cmd(
            "Sending file to cache",
            self.mk_rsync_cmd(
                src=str(source_path),
                dest=intermediary_file,
            ),
        )
        self.run_cmd(
            "Moving file to final location",
            [
                "mv",
                f"~/{intermediary_file}",
                target_path,
            ],
            ssh=True,
            sudo=True,
        )

    def download_file(self, remote_path: str, destination_path: Path) -> None:
        """Download a file in target using an intermediary cached dir for handling perms."""
        self._ensure_cached_dir()
        remote_cached_path = f"~/{_CACHE_PATH}/{uuid4().hex}"
        self.run_cmd(
            "Copying data to cache",
            [
                "install",
                "-m",
                "644",
                remote_path,
                remote_cached_path,
            ],
            ssh=True,
            sudo=True,
        )
        self.run_cmd(
            "Downloading remote file",
            self.mk_rsync_cmd(
                src=remote_cached_path,
                dest=str(destination_path),
                remote_dest=False,
            ),
        )

    def run_cmd(
        self,
        desc: str,
        cmd: ShellCommand | list[str],
        *,
        ok_codes: tuple[int, ...] = (0,),
        ssh: bool = False,
        sudo: bool = False,
        use_render_str: str | None = None,
        env: dict[str, str] | None = None,
    ):
        """Run a command.

        Args:
            desc: Command description.
            cmd: Command instance to run.
            ok_codes: Acceptable return codes without raising an error.
            ssh: If set, will wrap command in `ctx.do_ssh`.
            sudo: If set, will wrap command in `ctx.do_sudo`.
            use_render_str: If set, will override default value for ShellCommandProgram.render_str - will only take effect when 'cmd' is of type 'list[str]'.
            env: Envvars dict for when 'cmd' is of type list[str].

        """
        wrapper: BaseCommandWrapper = PassthroughCommandWrapper()
        if ssh:
            wrapper |= self.ctx.do_ssh
        if sudo:
            wrapper |= self.ctx.do_sudo
        if isinstance(cmd, list):
            if not cmd:
                msg = "Cannot run an empty command"
                raise self.mk_error(msg)
            head, *args = cmd
            program = ShellCommandProgram(
                shell_args=[head], render_str=use_render_str or head
            )
            cmd = ShellCommand(
                program=program,
                args=args,
                env=env or {},
            )
        _ = self.cli.run_command(
            command=wrapper | cmd,
            description=desc,
            error_cls=self.error_cls,
            ok_codes=ok_codes,
        )

    def mk_error(self, msg: str) -> InstallStepError:
        """Generate error with message."""
        return self.error_cls(msg)


@define(kw_only=True)
class Installer:
    ctx: InstallerContext
    cli: CLIPanelFactory
    steps: Sequence[type[InstallStep]]

    def run(self) -> None:
        """Execute all steps."""
        self.cli.info("Starting installation")
        t_0 = time()
        done_steps: int = 0
        for step_type in self.steps:
            step = step_type(ctx=self.ctx, cli=self.cli)
            suffix = f"{step.name} - {step.description}"
            if step.should_skip():
                self.cli.info(f"Skipping: {suffix}")
                continue
            t_step_0 = time()
            self.cli.info(f"Running: {suffix}")
            step.execute()
            t_step_f = time()
            t_step_delta = t_step_f - t_step_0
            done_steps += 1
            self.cli.info(f"Done {step.name} in {t_step_delta:.2f}s")
        t_f = time()
        t_delta = t_f - t_0

        self.cli.info(
            f"Finished installation in {done_steps} steps - time taken is {t_delta:.2f}s"
        )
