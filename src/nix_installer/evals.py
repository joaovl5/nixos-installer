import json
import shlex
from abc import ABC, abstractmethod
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, override

from attrs import define
from coisas.commands.nix import NixCommand
from coisas.commands.shell import ShellCommand, ShellCommandProgram

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from coisas.cli.cli import CLIPanelFactory


class NixRefError(RuntimeError): ...


class BaseNixRef(ABC):
    def _mk_nix_cmd(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> NixCommand:
        return NixCommand(
            args=list(args),
            env=env or {},
        )

    def _run_nix_capture(
        self,
        *,
        cli: CLIPanelFactory,
        description: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
    ) -> str:
        with TemporaryDirectory() as tmp_dir_str:
            output_path = Path(tmp_dir_str) / "stdout"
            nix_cmd = self._mk_nix_cmd(args, env=env)
            arg = (
                f"{shlex.join(nix_cmd.build())} > "
                f"{shlex.quote(str(output_path))}"
            )
            _ = cli.run_command(
                command=ShellCommand(
                    program=ShellCommandProgram(
                        shell_args=["sh", "-c"],
                        render_str="nix",
                    ),
                    args=[arg],
                ),
                description=description,
                error_cls=NixRefError,
            )
            return output_path.read_text().strip()

    def build_drv(
        self,
        *,
        cli: CLIPanelFactory,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> Path:
        """Assume ref is a derivation and build it, returning its path."""
        with TemporaryDirectory() as tmp_dir_str:
            out_link = Path(tmp_dir_str) / "result"
            _ = cli.run_command(
                command=self._mk_nix_cmd(
                    [
                        "build",
                        *extra_args,
                        *self.render_nix_eval_ref(),
                        "--out-link",
                        str(out_link),
                    ],
                    env=env,
                ),
                description="Building Nix derivation",
                error_cls=NixRefError,
            )
            if not out_link.exists():
                msg = "build produced no output path"
                raise NixRefError(msg)
            return out_link.resolve()

    def run_eval_raw(
        self,
        *,
        cli: CLIPanelFactory,
        apply: str | None = "toString",
        json_output: bool = False,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run eval and return raw stdout."""
        return self._run_nix_capture(
            cli=cli,
            description="Running Nix eval",
            args=[
                "eval",
                *extra_args,
                *(["--json"] if json_output else []),
                *(["--apply", apply] if apply else []),
                *self.render_nix_eval_ref(),
            ],
            env=env,
        )

    def run_eval_as_str(
        self,
        *,
        cli: CLIPanelFactory,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> str:
        """Run eval with 'toString' applied."""
        return self.run_eval_raw(
            cli=cli,
            extra_args=extra_args,
            env=env,
        ).strip('"')

    def run_eval_json(
        self,
        *,
        cli: CLIPanelFactory,
        apply: str | None = None,
        extra_args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> object:
        """Run eval as JSON and parse the result."""
        return json.loads(
            self.run_eval_raw(
                cli=cli,
                apply=apply,
                json_output=True,
                extra_args=extra_args,
                env=env,
            )
        )

    @abstractmethod
    def render_nix_eval_ref(self) -> list[str]:
        """Render ref into suffix for 'nix eval' commands."""


class NixRef:
    @define
    class File(BaseNixRef):
        """Equivalent to --file <file> <attr-path>."""

        file: Path
        attr_path: str

        @override
        def render_nix_eval_ref(self) -> list[str]:
            return ["--file", str(self.file), self.attr_path]

    @define
    class Flake(BaseNixRef):
        """Equivalent to <flake-dir-path>#<attr-path>."""

        dir: Path
        attr_path: str

        @override
        def render_nix_eval_ref(self) -> list[str]:
            return [f"{self.dir}#{self.attr_path}"]

    @define
    class Expr(BaseNixRef):
        """Equivalent to --expr <expr>."""

        expr: str

        @override
        def render_nix_eval_ref(self) -> list[str]:
            return ["--expr", self.expr]

    type _IsNixEval[T: BaseNixRef] = T
    type Type = _IsNixEval[File | Flake | Expr]
