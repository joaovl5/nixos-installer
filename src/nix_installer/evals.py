import json
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from attrs import define


class NixRefError(RuntimeError): ...


NIX_FLAGS = ["--option", "experimental-features", "nix-command flakes"]


class BaseNixRef(ABC):
    def build_drv(self) -> Path:
        """Assume ref is a derivation and build it, returning its path."""
        result = subprocess.run(
            [
                "nix",
                *NIX_FLAGS,
                "build",
                *self.render_nix_eval_ref(),
                "--no-link",
                "--print-out-paths",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"build failed, exit code was {result.returncode} - stderr was: {result.stderr}"
            raise NixRefError(msg)
        store_path_str = result.stdout.strip()
        if not store_path_str:
            msg = "build produced no output path"
            raise NixRefError(msg)
        return Path(store_path_str)

    def run_eval_raw(
        self,
        *,
        apply: str | None = "toString",
        json_output: bool = False,
    ) -> str:
        """Run eval and return raw stdout."""
        result = subprocess.run(
            [
                "nix",
                *NIX_FLAGS,
                "eval",
                *(["--json"] if json_output else []),
                *(["--apply", apply] if apply else []),
                *self.render_nix_eval_ref(),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            msg = f"eval failed, exit code was {result.returncode} - stderr was: {result.stderr}"
            raise NixRefError(msg)
        return result.stdout.strip()

    def run_eval_as_str(self) -> str:
        """Run eval with 'toString' applied."""
        return self.run_eval_raw().strip('"')

    def run_eval_json(self, *, apply: str | None = None) -> object:
        """Run eval as JSON and parse the result."""
        return json.loads(
            self.run_eval_raw(apply=apply, json_output=True)
        )

    @abstractmethod
    def render_nix_eval_ref(self) -> list[str]:
        """Render ref into suffix for 'nix eval' commands."""


class NixRef:
    type _IsNixEval[T: BaseNixRef] = T
    type Type = _IsNixEval[File | Flake | Expr]

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
