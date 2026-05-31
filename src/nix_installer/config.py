from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from coisas.uris import GitURIType


class SourceConfig(BaseModel):
    uri: GitURIType


class UseDiskEncryptionAction(BaseModel):
    # 1) host fetches and decrypts secret for disk encryption at 'data' source
    # 2) secret is sent to target
    # (this assumes usage of SOPS)
    sops_file: Path = Path("secrets/hosts.yaml")  # relative to 'data'
    sops_age_keyfile: Path | None = None
    sops_key: str | list[str] | None = (
        # if null, will be assumed to be the host name
        # do NOT add the '["<thing>"]' syntax, only use the key name
        # if you need nested keys, use a tuple/list of strings
        # so ["a", "b", "c"] becomes '["a"]["b"]["c"]'
        None
    )
    sent_keyfile_location: str = "/tmp/secret.key"  # in target


class FetchFacterConfig(BaseModel):
    # 1) gets nixos-facter report as a json file
    # 2) puts it at 'data' repo, at some 'save_folder', using the 'host'
    #    for the current install as the filename
    save_folder: Path = Field(  # relative to 'data'
        default_factory=lambda: Path("facter/")
    )


class FetchSSHKeyPairConfig(BaseModel):
    # 1) target generates ssh keypair
    # 2) we fetch its public part and save at 'data', at 'save_folder',
    #    using the 'host' of the current install as the filename
    # 3) private bit is copied post-install to 'target_copy_folder'
    # 4) if 'target_copy_to_user' is set, will also copy config into target
    #    user's home

    save_folder: Path = Field(default_factory=lambda: Path("ssh/"))
    keyfile_name: str = "main"
    target_copy_folder: str = "root/.ssh/"  # '/mnt/' gets prepended
    target_copy_to_user: bool = True


class FetchHostDataAction(BaseModel):
    # 1) gets some data from the host
    # 2) pushes it in a single-pass to the 'data' repo
    facter: FetchFacterConfig | None = Field(
        default_factory=FetchFacterConfig
    )
    ssh_keypair: FetchSSHKeyPairConfig | None = Field(
        default_factory=FetchSSHKeyPairConfig,
    )


class RefreshDepsAction(BaseModel):
    # 1) uses 'install' repo as basis and executes a command
    # 2) commits+pushes changes unless commit/push are disabled
    command: str


class ActionsConfig(BaseModel):
    use_disk_encryption: UseDiskEncryptionAction | None = Field(
        default_factory=UseDiskEncryptionAction,
    )
    fetch_host_data: FetchHostDataAction | None = Field(
        default_factory=FetchHostDataAction,
    )
    refresh_deps: RefreshDepsAction | None = None


class GitConfig(BaseModel):
    commit: bool = False
    push: bool = False
    commit_user_name: str = "nixos_installer"
    commit_user_email: str = "nixos_installer@local"
    commit_msg: str = (
        "[installer] update data for $HOST"  # gets substituted later
    )


class AppConfig(BaseModel):
    install: SourceConfig = Field(
        description="Houses installable configurations."
    )
    data: SourceConfig = Field(
        description="Houses secret or machine-specific data."
    )
    actions: ActionsConfig = Field(
        description="Describes configurable actions that are handled during install.",
        default_factory=ActionsConfig,
    )
    git: GitConfig = Field(
        description="Behavior for git actions.",
        default_factory=GitConfig,
    )
