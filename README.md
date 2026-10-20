# bootstrap-launcher

A small public POSIX-shell entry point for a setup script in a **private** GitHub
repository. The launcher contains no inventory, SSH keys, personal configuration,
or credentials. The target repository stays private.

## TL;DR

Run from your normal non-root login account, replacing `OWNER/PRIVATE-REPO`:

```sh
curl -fsSL https://raw.githubusercontent.com/OrangeSpyderMan/bootstrap-launcher/main/bootstrap.sh | sh -s -- --repo OWNER/PRIVATE-REPO -- --apply
```

Or:

```sh
wget -qO- https://raw.githubusercontent.com/OrangeSpyderMan/bootstrap-launcher/main/bootstrap.sh | sh -s -- --repo OWNER/PRIVATE-REPO -- --apply
```

This offers to install missing launcher prerequisites, uses existing GitHub
authentication or offers browser login, downloads one resolved commit, and runs
that repository's `setup.sh --apply`. The `--apply` meaning belongs to your setup
script. Omit it if that script defaults to a preview. The launcher itself can
still offer prerequisite installation and authentication.

No GitHub token is needed to download this public launcher. Access to the
private target is still required. Prompts use the controlling terminal, so
piping the script does not consume answers as code. Without a terminal,
authentication and installation choices must already be explicit.

## What it does

1. Validate repository/entry-point arguments and the non-root login account.
2. Detect a supported platform and offer missing Python 3.11+ and GitHub CLI
   packages through its existing package manager. No third-party package
   repositories or signing keys are added.
3. Verify the authenticated account with `gh api user` (rather than relying on
   `gh auth status`, which can return success without a login in older versions).
   Reuse `GH_TOKEN`/`GITHUB_TOKEN` or an existing login, or ask before running the
   official `gh auth login --web` flow. Verify repository access separately
   before resolving the revision.
4. Resolve the requested branch/tag/commit once using `gh api`, print the full
   commit SHA, and download that exact repository archive.
5. Reject archive links, special files and traversal; extract into a private
   temporary directory; run the requested shell entry point as the login user.
6. Remove the temporary checkout on exit and propagate the setup script's exit
   status. Prerequisites and any changes made by the setup script remain.

Git is not required by the launcher. The repository's setup script owns all
actual host configuration, package choices, keys, backups and application logic.
Relative paths in that script resolve from the downloaded repository root.
Archives are limited to 10,000 entries and 100 MiB of uncompressed content;
repositories containing symlinks are currently unsupported.

## Authentication

For a personal workstation, reuse `gh` or consent to browser login. `gh` manages
the authentication flow and credential storage. On a minimal system it can fall
back to plaintext storage; the launcher explains this before asking to log in.
The login command selects HTTPS and uses flags available in Debian trixie
GitHub CLI 2.46; no SSH-key upload flag is required. The resulting login remains
on the host. It is not silently logged out, since
that could disrupt existing tools.

For one-time server setup, supply an **expiring, repository-scoped read-only
token** from your password manager as exported `GH_TOKEN` (or `GITHUB_TOKEN`).
Repository contents read access is sufficient for commit/archive retrieval.
The launcher uses the token through GitHub CLI, never puts it in command-line
arguments, and does not persist it. Invalid supplied tokens fail rather than
falling back to a different personal identity.

The token variables are removed from the environment before the target script
runs. This is not a sandbox: a script running as you can still access your files,
and an existing stored `gh` login remains accessible. Only run setup code from
a repository/revision you trust. A SHA identifies the downloaded snapshot; it
is not a signature or independent proof of publisher identity.

## Options

```text
--repo OWNER/REPO             Required, also accepts an HTTPS github.com URL
--ref REF                    Branch/tag/commit; default main
--script PATH                Relative shell entry point; default setup.sh
--no-prompt                  Disable launcher prompts; require existing auth
--install-prerequisites      Authorize missing Python/gh installation
--privilege-tool sudo|doas   Otherwise detected automatically
--help                       Show help
--                           Everything after this belongs to the target script
```

Unattended use with an exported repository read token:

```sh
sh bootstrap.sh --repo OWNER/PRIVATE-REPO --ref FULL-COMMIT-SHA \
  --no-prompt --install-prerequisites -- --apply --no-prompt
```

The second `--no-prompt` is a target-script option, if supported. Launcher flags
are not forwarded. With launcher `--no-prompt`, missing prerequisites cause an
error unless `--install-prerequisites` is also given; missing credentials always
cause an error. Privilege escalation may still require authentication.

You can download/inspect the public script before running it, or pin its URL to
a tag/full commit instead of `main`. `--ref` selects the target revision, not the
public launcher's revision. Only github.com is supported in this initial version.

## Platform support

| Platform | Package manager | GitHub CLI package | Python prerequisite |
| --- | --- | --- | --- |
| Debian / Ubuntu | APT | `gh` | `python3` |
| Arch Linux | pacman | `github-cli` | `python` |
| Fedora (mutable systems) | DNF | `gh` | `python3` |
| FreeBSD | pkg | `gh` | `python311` |

An existing supported Python is reused. Unknown distributions, immutable Fedora
and package repositories unable to provide Python 3.11+ fail explicitly.
Elevation is used only for prerequisite installation; browser auth and downloaded
setup run as the login user. Account creation/elevation setup is an OS-install
prerequisite.

Arch installations use the existing package database and `pacman -S --needed`;
they never run `pacman -Sy` or perform a full system upgrade automatically. Keep
the host current with a normal full upgrade before using the launcher.

## Development

```sh
sh -n bootstrap.sh
python3 -m unittest discover -s tests -v
```

Tests use simulated package managers and GitHub CLI responses. They cover
package mapping, authentication selection, argument forwarding, streamed
terminal prompts, failed/incomplete downloads, unsafe archives and cleanup.
They never install packages, authenticate real accounts or contact GitHub.
Platform paths beyond the maintainer's host still need real-host testing.

References: [GitHub CLI login](https://cli.github.com/manual/gh_auth_login),
[GitHub CLI API](https://cli.github.com/manual/gh_api),
[GitHub CLI platform packages](https://github.com/cli/cli/blob/trunk/docs/install_linux.md).
