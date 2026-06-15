from pathlib import Path

from pydantic import BaseModel, Field


class SSHParams(BaseModel):
    identity: Path
    host: str
    port: int = 22
    sudo: bool = True


class NixOSParams(BaseModel):
    host: str = Field(
        description="Name of the 'nixosConfiguration' to use.",
    )


class AppParams(BaseModel):
    ssh: SSHParams
    nixos: NixOSParams
    config: Path | None = Field(
        default=None,
        description="Optional config.toml path override.",
    )
