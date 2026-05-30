from attrs import define


@define
class InstallerContext:
    pass


@define
class Installer:
    ctx: InstallerContext
