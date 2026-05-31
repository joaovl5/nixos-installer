from coisas.cli.cli import CLIPanelFactory
from coisas.main import run
from cyclopts import App
from rich.console import Console
from rich.traceback import install as install_rich_traceback

from nix_installer.installer import (
    Installer,
    InstallerContext,
)
from nix_installer.steps import (
    CommitAndPushStep,
    ConfigureSubstitutersStep,
    DownloadFacterStep,
    NixOSInstallStep,
    RefreshDepsStep,
    RunDiskoStep,
    RunFacterStep,
)

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


def _make_installer(
    ctx: InstallerContext,
    cli: CLIPanelFactory,
) -> Installer:
    return Installer(
        ctx=ctx,
        cli=cli,
        steps=[
            ConfigureSubstitutersStep,
            RunDiskoStep,
            RunFacterStep,
            DownloadFacterStep,
            CommitAndPushStep,
            RefreshDepsStep,
            NixOSInstallStep,
        ],
    )


def main() -> None:
    run("echo 'Hello from nix-installer!'")
