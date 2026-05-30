from pathlib import Path

from coisas.main import run
from cyclopts import App
from pydantic import BaseModel, Field
from rich.console import Console
from rich.traceback import install as install_rich_traceback

console = Console()
error_console = Console(stderr=True)
app = App(
    name="nix-installer",
    console=console,
    error_console=error_console,
)
_ = install_rich_traceback(
    console=error_console,
)


class SSHParams(BaseModel):
    identity: Path
    host: str
    sudo: bool = True


class NixOSParams(BaseModel):
    cfg: str = Field(description="Name of the 'nixosConfiguration' to use.")


class AppParams(BaseModel):
    ssh: SSHParams
    nixos: NixOSParams


def main() -> None:
    run("echo 'Hello from nix-installer!'")
