from typing import Annotated

from coisas.cli.cli import CLIPanelFactory
from cyclopts import App, Parameter
from rich.console import Console
from rich.traceback import install as install_rich_traceback

from nix_installer.config import read_config_file
from nix_installer.installer import (
    Installer,
    InstallerContext,
)
from nix_installer.params import (
    AppParams,  # noqa: TC001 - Cyclopts needs this at runtime for CLI parsing
)
from nix_installer.steps import (
    CommitAndPushStep,
    ConfigureSubstitutersStep,
    DownloadFacterStep,
    GenerateSSHKeysStep,
    NixOSInstallStep,
    RefreshDepsStep,
    RunDiskoStep,
    RunFacterStep,
    SendKeyFileStep,
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
            SendKeyFileStep,
            RunDiskoStep,
            RunFacterStep,
            DownloadFacterStep,
            GenerateSSHKeysStep,
            CommitAndPushStep,
            RefreshDepsStep,
            NixOSInstallStep,
        ],
    )


@app.default
def install(params: Annotated[AppParams, Parameter(name="*")]) -> None:
    """Run the NixOS installer."""
    config = read_config_file(params.config)
    ctx = InstallerContext(
        params=params,
        config=config,
    )
    cli = CLIPanelFactory(console=console)
    installer = _make_installer(ctx=ctx, cli=cli)
    installer.run()


def main() -> None:
    """Run the CLI app."""
    app()
