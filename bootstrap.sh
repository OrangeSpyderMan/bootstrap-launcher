#!/bin/sh
# A generic public entry point for a setup script in a private GitHub repo.
set -eu
set -f

# Define everything before executing it: pipe input is code, not prompt input.
bootstrap_main() {
    fail() { printf '%s\n' "Bootstrap failed: $*" >&2; exit 1; }
    usage() {
        cat <<'HELP'
Usage: sh bootstrap.sh --repo OWNER/REPO [options] -- [setup arguments...]
       curl -fsSL URL | sh -s -- --repo OWNER/REPO -- [setup arguments...]

  --repo REPO                Required: OWNER/REPO or HTTPS github.com URL
  --ref REF                  Branch, tag or commit (default: main)
  --gh-version VERSION       Upstream stable version or latest (Linux; default latest)
  --script PATH              Shell entry point in the repo (default: setup.sh)
  --no-prompt                Disable launcher prompts; require existing auth
  --install-prerequisites    Authorize missing gh/Python installation without prompts
  --privilege-tool TOOL      sudo or doas (otherwise auto-detect)
  --help                     Show this help

Arguments after -- belong to the downloaded setup script, including its own
--apply, --select or --no-prompt options. Launcher flags are not forwarded.
Run as a normal login user. Authentication uses GH_TOKEN/GITHUB_TOKEN, an
existing gh login, or explicit consent to gh's browser login. No token is
printed or saved by this launcher. Browser login uses gh's credential storage.
HELP
    }

    repo=
    ref=main
    gh_version=latest
    entry=setup.sh
    no_prompt=no
    install_prerequisites=no
    privilege=
    while [ "$#" -gt 0 ]; do
        case $1 in
            --help|-h) usage; exit 0 ;;
            --) shift; break ;;
            --no-prompt) no_prompt=yes; shift ;;
            --install-prerequisites) install_prerequisites=yes; shift ;;
            --repo|--ref|--script|--privilege-tool|--gh-version)
                [ "$#" -ge 2 ] && [ -n "$2" ] || fail "$1 requires a value"
                case $1 in
                    --repo) repo=$2 ;;
                    --ref) ref=$2 ;;
                    --gh-version) gh_version=$2 ;;
                    --script) entry=$2 ;;
                    --privilege-tool) privilege=$2 ;;
                esac
                shift 2 ;;
            *) fail "Unknown launcher argument: $1 (put target arguments after --)" ;;
        esac
    done
    [ -n "$repo" ] || fail '--repo OWNER/REPO is required'
    case $repo in https://github.com/*) repo=${repo#https://github.com/} ;; esac
    repo=${repo%/}
    repo=${repo%.git}
    case $repo in ''|*[!a-zA-Z0-9_./-]*|/*|*/|*/*/*|*..*) fail 'Expected GitHub OWNER/REPO' ;; esac
    case $repo in */*) ;; *) fail 'Expected GitHub OWNER/REPO' ;; esac
    case $entry in ''|*[!a-zA-Z0-9_./-]*|/*|..|.|../*|*/../*|*/..)
        fail 'Entry point must be a safe relative shell-script path' ;;
    esac
    case $privilege in ''|sudo|doas) ;; *) fail 'Privilege tool must be sudo or doas' ;; esac

    [ "$(id -u)" != 0 ] || fail 'Log in as your normal user; elevation is requested only for prerequisites.'
    [ -n "${HOME:-}" ] && [ -d "$HOME" ] && [ -w "$HOME" ] && [ ! -L "$HOME" ] || fail 'HOME must be an existing writable directory, not a symlink.'
    [ "$(find "$HOME" -prune -user "$(id -u)")" = "$HOME" ] || fail 'HOME must be owned by the login user.'

    case $(uname -s) in
        Linux)
            [ -r /etc/os-release ] || fail 'Missing /etc/os-release'
            . /etc/os-release
            case ${ID:-} in
                debian|ubuntu) manager=apt; manager_command=apt-get; python_package=python3 ;;
                arch) manager=pacman; manager_command=pacman; python_package=python ;;
                fedora)
                    [ ! -e /run/ostree-booted ] || fail 'Immutable Fedora systems are unsupported.'
                    manager=dnf; manager_command=dnf; python_package=python3 ;;
                *) fail "Unsupported distribution: ${ID:-unknown}" ;;
            esac ;;
        FreeBSD) manager=pkg; manager_command=pkg; gh_package=gh; python_package=python311 ;;
        *) fail 'Supported systems: Debian, Ubuntu, Arch Linux, Fedora with DNF, FreeBSD.' ;;
    esac
    command -v "$manager_command" >/dev/null 2>&1 || fail "$manager_command is unavailable"
    printf 'Platform backend: %s\n' "$manager"
    if [ -n "$privilege" ]; then
        command -v "$privilege" >/dev/null 2>&1 || fail "$privilege is unavailable"
    elif command -v sudo >/dev/null 2>&1; then privilege=sudo
    elif command -v doas >/dev/null 2>&1; then privilege=doas
    fi

    confirm() {
        [ "$no_prompt" = no ] && [ -t 0 ] || fail 'A terminal is needed; use --no-prompt with explicit prerequisites/authentication.'
        printf '%s [y/N] ' "$1"
        read -r answer
        case $answer in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
    }
    apt_updated=no
    install_packages() {
        if [ "$install_prerequisites" != yes ]; then
            [ "$no_prompt" = no ] || fail "Missing prerequisites: $*. Add --install-prerequisites to authorize installation."
            confirm "Install missing prerequisites: $*?" || fail 'Prerequisite installation declined.'
        fi
        [ -n "$privilege" ] || fail 'Installing prerequisites needs sudo or doas configured during OS installation.'
        "$privilege" true || fail 'Privilege escalation failed.'
        case $manager in
            apt)
                if [ "$apt_updated" = no ]; then "$privilege" apt-get update; apt_updated=yes; fi
                "$privilege" apt-get install -y "$@" ;;
            pacman) "$privilege" pacman -S --needed --noconfirm "$@" ;;
            dnf) "$privilege" dnf install -y "$@" ;;
            pkg) "$privilege" pkg install -y "$@" ;;
        esac
    }
    python=
    find_python() {
        for candidate in python3 python3.15 python3.14 python3.13 python3.12 python3.11; do
            if command -v "$candidate" >/dev/null 2>&1 &&
                "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
                python=$candidate
                return 0
            fi
        done
        return 1
    }
    if ! find_python; then
        install_packages "$python_package"
        find_python || fail 'The platform repositories did not provide Python 3.11+; install it and rerun.'
    fi
    "$python" -c 'import os,pwd,pathlib,sys; sys.exit(0 if pathlib.Path.home() == pathlib.Path(pwd.getpwuid(os.getuid()).pw_dir) else "HOME differs from the login account home")'
    if [ "$manager" = pkg ]; then
        # Upstream does not publish FreeBSD binaries; retain its native package.
        if ! command -v gh >/dev/null 2>&1; then install_packages "$gh_package"; fi
        gh_bin=$(command -v gh) || fail 'GitHub CLI installation failed.'
    else
        gh_bin=$("$python" - --install-gh "$HOME" "$gh_version" "$install_prerequisites" "$no_prompt" 3<&0 <<'GH_PY'
import hashlib
import io
import json
import os
import pathlib
import platform
import re
import sys
import tarfile
import tempfile
import urllib.request


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):
            raise ValueError('Refusing release download redirect away from HTTPS')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit=100 * 1024 * 1024):
    if not url.startswith(('https://api.github.com/repos/cli/cli/releases/',
                           'https://github.com/cli/cli/releases/download/')):
        raise ValueError('Unexpected upstream release URL')
    opener = urllib.request.build_opener(HTTPSRedirect())
    with opener.open(url, timeout=60) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Upstream response exceeds size limit')
    return content


def ensure_gh(home, requested, authorized, no_prompt):
    architectures = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64',
                     'arm64': 'arm64', 'i386': '386', 'i686': '386', 'armv7l': 'armv6'}
    architecture = architectures.get(platform.machine().lower())
    if architecture is None:
        raise ValueError('No supported upstream GitHub CLI binary for this CPU')
    if requested != 'latest' and not re.fullmatch(r'v?\d+\.\d+\.\d+', requested):
        raise ValueError('--gh-version must be latest or a stable version such as 2.102.0')
    endpoint = 'latest' if requested == 'latest' else 'tags/v' + requested.removeprefix('v')
    release = json.loads(fetch('https://api.github.com/repos/cli/cli/releases/' + endpoint, 1024 * 1024))
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected an official stable GitHub CLI release')
    version = tag[1:]
    name = f'gh_{version}_linux_{architecture}.tar.gz'
    assets = {item['name']: item['browser_download_url'] for item in release['assets']}
    checksums = f'gh_{version}_checksums.txt'
    if name not in assets or checksums not in assets:
        raise ValueError('Official release lacks a matching binary or checksum file')
    directory = home / '.local/lib/bootstrap-launcher/gh' / f'{version}-linux-{architecture}'
    binary = directory / 'bin/gh'
    record = directory / 'binary.sha256'
    for parent in [binary, record, *binary.parents]:
        if parent == home.parent:
            break
        if parent.is_symlink() or (parent.exists() and (parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o022)):
            raise ValueError('GitHub CLI cache must be owned by you and contain no symlink or group/world-writable paths')
    if binary.is_file() and record.is_file() and os.access(binary, os.X_OK):
        if hashlib.sha256(binary.read_bytes()).hexdigest() == record.read_text().strip():
            return binary
    if not authorized:
        if no_prompt or not os.isatty(3):
            raise ValueError('Upstream GitHub CLI installation needs --install-prerequisites (or an interactive confirmation)')
        print(f'Install official GitHub CLI {tag} for this user? [y/N] ', end='', file=sys.stderr, flush=True)
        with os.fdopen(os.dup(3)) as terminal:
            if terminal.readline().strip().lower() not in ('y', 'yes'):
                raise ValueError('GitHub CLI installation declined')
    checksum_text = fetch(assets[checksums], 1024 * 1024).decode()
    matches = [line.split()[0] for line in checksum_text.splitlines()
               if len(line.split()) == 2 and line.split()[1].lstrip('*') == name]
    if len(matches) != 1 or not re.fullmatch(r'[0-9a-fA-F]{64}', matches[0]):
        raise ValueError('No unique SHA-256 checksum for the GitHub CLI archive')
    archive = fetch(assets[name])
    if hashlib.sha256(archive).hexdigest() != matches[0].lower():
        raise ValueError('GitHub CLI release checksum mismatch; nothing installed')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as stream:
        member = stream.getmember(f'gh_{version}_linux_{architecture}/bin/gh')
        if not member.isfile() or member.size > 100 * 1024 * 1024:
            raise ValueError('Expected an ordinary GitHub CLI binary in the archive')
        content = stream.extractfile(member).read()
    old_umask = os.umask(0o077)
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        binary.parent.mkdir(exist_ok=True, mode=0o700)
    finally:
        os.umask(old_umask)
    descriptor, temporary = tempfile.mkstemp(prefix='.gh-', dir=binary.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
            os.fchmod(output.fileno(), 0o755)
        os.replace(temporary, binary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    with os.fdopen(os.open(record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as output:
        output.write(hashlib.sha256(content).hexdigest() + '\n')
    print(f'Installed official GitHub CLI {tag}; release SHA-256 verified.', file=sys.stderr)
    return binary


if __name__ == '__main__':
    try:
        _, home, requested, authorized, no_prompt = sys.argv[1:]
        print(ensure_gh(pathlib.Path(home), requested, authorized == 'yes', no_prompt == 'yes'))
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        print('GitHub CLI setup failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
GH_PY
        ) || fail 'Could not prepare the official GitHub CLI release.'
    fi
    PATH=${gh_bin%/*}:$PATH
    export PATH

    # Keep requests on github.com and prevent inherited HTTP-debug logging.
    GH_HOST=github.com
    export GH_HOST
    unset GH_DEBUG
    if [ -n "${GH_TOKEN:+present}${GITHUB_TOKEN:+present}" ]; then
        account=$(gh api --hostname github.com user --jq .login) || fail 'Supplied GitHub token could not authenticate. Check the token; browser login was not attempted.'
        printf 'Using supplied GitHub token for %s (not persisted).\n' "$account"
    elif account=$(gh api --hostname github.com user --jq .login 2>/dev/null) && [ -n "$account" ]; then
        # gh 2.46 can report auth-status success without a login when GH_HOST
        # is set. Verify an authenticated endpoint instead of trusting status.
        printf 'Using existing GitHub CLI login: %s.\n' "$account"
    else
        [ "$no_prompt" = no ] || fail 'No GitHub authentication. Supply GH_TOKEN/GITHUB_TOKEN or run gh auth login first.'
        printf '%s\n' 'Browser login may persist personal credentials on this host, using gh storage (which can fall back to a plaintext file).'
        confirm 'Continue with GitHub browser login on this host?' || fail 'Authentication declined. Use a repository-scoped read-only token instead.'
        gh auth login --hostname github.com --web --git-protocol https
        account=$(gh api --hostname github.com user --jq .login) || fail 'GitHub browser login did not produce a usable authenticated account.'
        printf 'Authenticated GitHub account: %s.\n' "$account"
    fi
    gh api --hostname github.com "repos/$repo" --jq .full_name >/dev/null || fail "Account $account cannot read repository $repo. Check the repo name, account and token repository access."

    umask 077
    work=$(mktemp -d "${TMPDIR:-/tmp}/bootstrap-launcher.XXXXXXXX")
    cleanup() { rm -rf "$work"; }
    trap cleanup 0
    trap 'exit 130' INT
    trap 'exit 143' TERM
    encoded_ref=$("$python" -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$ref")
    commit=$(gh api --hostname github.com "repos/$repo/commits/$encoded_ref" --jq .sha) || fail "Repository access was verified, but revision $ref could not be resolved. Check --ref or retry after a network failure."
    case $commit in ''|*[!0-9a-f]*) fail 'GitHub returned an invalid commit SHA.' ;; esac
    [ "${#commit}" -eq 40 ] || fail 'GitHub returned an invalid commit SHA.'
    printf 'Repository: %s; ref: %s; resolved commit: %s\n' "$repo" "$ref" "$commit"
    gh api --hostname github.com "repos/$repo/tarball/$commit" > "$work/source.tar.gz" || fail 'Archive download failed.'
    mkdir "$work/source"
    "$python" - "$work/source.tar.gz" "$work/source" <<'PY'
import pathlib
import shutil
import sys
import tarfile

archive, destination = sys.argv[1:]
root = pathlib.Path(destination)
with tarfile.open(archive, "r:gz") as stream:
    members = stream.getmembers()
    if len(members) > 10000 or sum(m.size for m in members) > 100 * 1024 * 1024:
        raise SystemExit("Repository archive exceeds extraction limits")
    for member in members:
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or member.size < 0 or not (member.isfile() or member.isdir()):
            raise SystemExit("Unsafe archive member (links and special files are not supported)")
    for member in members:
        target = root / member.name
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o755)
roots = list(root.iterdir())
if len(roots) != 1 or not roots[0].is_dir():
    raise SystemExit("Expected exactly one repository directory")
PY
    root=$("$python" -c 'import pathlib,sys; print(next(pathlib.Path(sys.argv[1]).iterdir()))' "$work/source")
    [ -f "$root/$entry" ] || fail "Repository has no shell entry point: $entry"
    printf 'Running %s as the login user.\n' "$entry"
    # The target receives a local checkout, not environment authentication tokens.
    # Existing gh credentials remain subject to the account's own gh configuration.
    (
        unset GH_TOKEN GITHUB_TOKEN
        cd "$root"
        sh "$entry" "$@"
    )
}

# Restore the original script input after the function returns.
if [ -t 0 ]; then
    bootstrap_main "$@"
elif ( : </dev/tty ) 2>/dev/null; then
    bootstrap_main "$@" </dev/tty
else
    bootstrap_main "$@"
fi
