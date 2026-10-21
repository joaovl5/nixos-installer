# `nixos-installer`

This is an installer script that I wrote for my own purposes on my personal NixOS homelab, but also should be flexible enough to suit other people's needs with relative ease. It's 'steps' are currently designed around my personal workflow, so this might need tweaking if used for other setups.

The currently-hardcoded sequence of steps is:

1. Configure Nix substituters in the remote target (copies from the NixOS config used as input)
2. (optional, if using disk encryption) Send key-file in the remote target; currently pulls it from a [sops] file
3. Run [disko] in the remote target for partitioning disk and related operations
4. (optional) Run the [nixos-facter] report script for preparing a report of the target's hardware
5. (optional) Download the facter report from step #4 into a local directory/repository
6. Generates an SSH keypair inside the target host, and downloads the public key into a local directory/repository (private key never leaves the host or is read); also copies those keys into the target's default username
7. (optional) Commit and push the downloaded data from steps #5 and #6, if the directory is a Git repository
8. (optional) Refresh dependencies in the input configuration (useful in case the repository pushed to in step #7 is different from the input configuration, in cases like a private secrets repo being used as inputs to a public NixOS configuration)
9. Run `nixos-install` from the remote target
10. Handle post-install fixes like chowning some files that require non-root permissions (e.g. the SSH keypair that was copied into the `/home/<user>/.ssh/` of the remote target)

[sops]: https://github.com/getsops/sops
[disko]: https://github.com/nix-community/disko
[nixos-facter]: https://github.com/nix-community/nixos-facter

The script currently has a lot of configuration/parametrization possibilities. The `config.example.toml` and `example.install.sh` files in the repository here serve as aid for that. An example of how I personally used these configurations was to locally iterate on the installer for my config without having to keep updating inputs, so I used a `NPINS_OVERRIDE__private` envvar to avoid that.
