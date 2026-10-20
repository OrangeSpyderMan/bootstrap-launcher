"""Offline tests: fake package managers/GitHub CLI; no host installs or login."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PYTHON = shutil.which("python3")


def executable(path, content):
    path.write_text("#!/bin/sh\n" + content + "\n")
    path.chmod(0o755)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="launcher-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.log = self.root / "commands"
        self.release = self.root / "os-release"
        self.release.write_text('ID=debian\nVERSION_ID="13"\n')
        self.script = self.root / "bootstrap.sh"
        self.script.write_text((ROOT / "bootstrap.sh").read_text().replace("/etc/os-release", str(self.release)))
        self.env = {key: value for key, value in os.environ.items() if key not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_DEBUG"}}
        self.env.update(PATH=str(self.bin), HOME=str(self.home), COMMAND_LOG=str(self.log),
                        FIXTURE_ARCHIVE=str(self.root / "source.tar.gz"), FAKE_AUTH_FILE=str(self.root / "logged-in"))
        for name in ("cat", "mkdir", "mktemp", "rm"):
            (self.bin / name).symlink_to(shutil.which(name))
        executable(self.bin / "id", "echo 1000")
        executable(self.bin / "find", 'printf "%s\\n" "$1"')
        executable(self.bin / "uname", 'echo Linux')
        executable(self.bin / "sudo", 'echo "sudo $*" >> "$COMMAND_LOG"\n"$@"')
        executable(self.bin / "doas", 'echo "doas $*" >> "$COMMAND_LOG"\n"$@"')
        executable(self.bin / "sh", 'exec /bin/sh "$@"')
        executable(self.bin / "python3", f'''
case "$*" in
  *pwd.getpwuid*) exit 0 ;;
  *" --install-gh "*)
    if [ ! -x "$PATH/gh" ]; then
      if [ "$5" != yes ]; then
        if [ "$6" = yes ] || [ ! -t 3 ]; then echo 'needs --install-prerequisites' >&2; exit 1; fi
        read -r answer <&3
        [ "$answer" = y ] || exit 1
      fi
      echo upstream-gh-install >> "$COMMAND_LOG"
      /bin/ln -s "$PATH/available-gh" "$PATH/gh"
    fi
    printf '%s/gh\\n' "$PATH"
    exit 0 ;;
esac
exec {PYTHON} "$@"
''')
        executable(self.bin / "gh", '''
echo "gh $*" >> "$COMMAND_LOG"
case "$*" in
  "auth status "*) exit 0 ;; # Reproduce gh 2.46 false-positive status.
  "auth login "*)
    # Model the flags accepted by Debian trixie's gh 2.46.
    shift 2
    while [ "$#" -gt 0 ]; do
      case $1 in
        --hostname|--git-protocol) shift 2 ;;
        --web) shift ;;
        *) echo "unknown login flag: $1" >&2; exit 2 ;;
      esac
    done
    [ -t 0 ] || exit 5
    : > "$FAKE_AUTH_FILE"
    echo "browser-login" ;;
  *" user --jq .login")
    if [ "${FAKE_AUTH_STATUS:-0}" != 0 ] && [ ! -f "$FAKE_AUTH_FILE" ]; then exit 1; fi
    echo fixture-user ;;
  *" --jq .full_name") [ "${FAKE_REPO_STATUS:-0}" = 0 ] || exit 1; echo example/private ;;
  *"/commits/"*) [ "${FAKE_REF_STATUS:-0}" = 0 ] || exit 1; printf '%040d\\n' 1 ;;
  *"/tarball/"*) cat "$FIXTURE_ARCHIVE" ;;
  *) exit 9 ;;
esac
''')
        for manager in ("apt-get", "dnf", "pacman", "pkg"):
            executable(self.bin / manager, f'''
echo "{manager} $*" >> "$COMMAND_LOG"
case "$*" in *python*) /bin/ln -s "$PATH/available-python" "$PATH/python3" ;; esac
case "$*" in *github-cli*|*gh*) /bin/ln -s "$PATH/available-gh" "$PATH/gh" ;; esac
''')
        self.archive()

    def archive(self, name="snapshot/setup.sh", kind=None, body=None):
        if body is None:
            body = b'''printf 'target-args:'
for argument do printf '<%s>' "$argument"; done
printf '\\n'
[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ] || exit 8
printf 'target-root:%s\\n' "$PWD"
'''
        with tarfile.open(self.root / "source.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo(name)
            member.mode = 0o755
            if kind is not None:
                member.type = kind
                member.linkname = "/tmp"
            else:
                member.size = len(body)
            archive.addfile(member, io.BytesIO(body) if kind is None else None)

    def run_launcher(self, *args, streamed=False):
        command = ["/bin/sh", "-s", "--"] if streamed else ["/bin/sh", str(self.script)]
        return subprocess.run([*command, *args], env=self.env, text=True,
                              input=self.script.read_text() if streamed else None,
                              capture_output=True, timeout=10)

    def commands(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_existing_login_fetches_one_commit_forwards_args_and_cleans(self):
        result = self.run_launcher("--repo", "example/private", "--ref", "feature/start", "--", "--apply", "two words")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("target-args:<--apply><two words>", result.stdout)
        self.assertIn("/commits/feature%2Fstart", self.commands())
        self.assertIn("/tarball/" + "0" * 39 + "1", self.commands())
        self.assertNotIn("auth login", self.commands())
        target = result.stdout.split("target-root:")[1].splitlines()[0]
        self.assertFalse(Path(target).exists())

    def test_token_auth_does_not_persist_or_forward_token(self):
        for variable in ("GH_TOKEN", "GITHUB_TOKEN"):
            with self.subTest(variable=variable):
                self.env[variable] = "not-a-real-token"
                result = self.run_launcher("--repo", "https://github.com/example/private.git", "--no-prompt")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("auth login", self.commands())
                self.assertNotIn("not-a-real-token", result.stdout + result.stderr + self.commands())
                self.env.pop(variable)

    def test_headless_without_auth_fails_before_repository_download(self):
        self.env["FAKE_AUTH_STATUS"] = "1"
        result = self.run_launcher("--repo", "example/private", "--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No GitHub authentication", result.stderr)
        self.assertNotIn("/commits/", self.commands())

    def test_invalid_supplied_token_never_falls_back_to_browser(self):
        self.env.update(GH_TOKEN="not-a-real-token", FAKE_AUTH_STATUS="1")
        result = self.run_launcher("--repo", "example/private")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Supplied GitHub token could not authenticate", result.stderr)
        self.assertNotIn("auth login", self.commands())
        self.assertNotIn("/commits/", self.commands())

    def test_repository_access_failure_is_distinct_from_bad_ref(self):
        self.env["FAKE_REPO_STATUS"] = "1"
        result = self.run_launcher("--repo", "example/private", "--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Account fixture-user cannot read repository", result.stderr)
        self.assertNotIn("/commits/", self.commands())
        self.env["FAKE_REPO_STATUS"] = "0"
        self.env["FAKE_REF_STATUS"] = "1"
        result = self.run_launcher("--repo", "example/private", "--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Repository access was verified", result.stderr)
        self.assertNotIn("/tarball/", self.commands())


    def test_missing_prerequisites_need_explicit_noninteractive_authorization(self):
        (self.bin / "gh").rename(self.bin / "available-gh")
        result = self.run_launcher("--repo", "example/private", "--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--install-prerequisites", result.stderr)
        self.assertNotIn("sudo", self.commands())

    def test_prerequisites_use_platform_packages(self):
        for distro, manager, gh_package, python_package in (
            ("debian", "apt-get", "gh", "python3"),
            ("ubuntu", "apt-get", "gh", "python3"),
            ("arch", "pacman", "github-cli", "python"),
            ("fedora", "dnf", "gh", "python3"),
            ("freebsd", "pkg", "gh", "python311"),
        ):
            with self.subTest(distro=distro):
                self.release.write_text(f'ID={distro}\n')
                executable(self.bin / "uname", 'echo FreeBSD' if distro == "freebsd" else 'echo Linux')
                (self.bin / "gh").rename(self.bin / "available-gh")
                (self.bin / "python3").rename(self.bin / "available-python")
                if self.log.exists():
                    self.log.unlink()
                result = self.run_launcher("--repo", "example/private", "--no-prompt", "--install-prerequisites")
                self.assertEqual(result.returncode, 0, result.stderr)
                commands = self.commands()
                prefix = f"{manager} -S --needed --noconfirm" if manager == "pacman" else f"{manager} install -y"
                if distro == "freebsd":
                    self.assertIn(prefix + " " + gh_package, commands)
                else:
                    self.assertIn("upstream-gh-install", commands)
                    self.assertNotIn(prefix + " " + gh_package, commands)
                self.assertIn(prefix + " " + python_package, commands)
                self.assertNotIn("-Sy", commands)
                self.assertEqual(commands.splitlines().count("apt-get update"), 1 if manager == "apt-get" else 0)
                (self.bin / "gh").unlink()
                (self.bin / "available-gh").rename(self.bin / "gh")
                (self.bin / "python3").unlink()
                (self.bin / "available-python").rename(self.bin / "python3")

    def test_root_unknown_distro_and_bad_paths_fail_early(self):
        for arguments in ([], ["--repo", "https://evil.example/a/b"], ["--repo", "a/b", "--script", "../bad"]):
            result = self.run_launcher(*arguments)
            self.assertNotEqual(result.returncode, 0)
        executable(self.bin / "id", "echo 0")
        self.assertIn("normal user", self.run_launcher("--repo", "a/b").stderr)
        executable(self.bin / "id", "echo 1000")
        self.release.write_text("ID=unknown\n")
        self.assertIn("Unsupported distribution", self.run_launcher("--repo", "a/b").stderr)
        self.assertEqual(self.commands(), "")

    def test_archive_links_and_traversal_are_rejected(self):
        for name, kind in (("../escape", None), ("snapshot/link", tarfile.SYMTYPE), ("snapshot/link", tarfile.LNKTYPE)):
            with self.subTest(name=name, kind=kind):
                self.archive(name=name, kind=kind)
                result = self.run_launcher("--repo", "example/private", "--no-prompt")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsafe archive", result.stderr)
                self.assertNotIn("target-root:", result.stdout)

    def test_download_failure_never_runs_target(self):
        executable(self.bin / "gh", 'exit 1')
        result = self.run_launcher("--repo", "example/private", "--no-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("target-root:", result.stdout)

    def test_target_failure_is_propagated(self):
        self.archive(body=b"exit 23\n")
        result = self.run_launcher("--repo", "example/private", "--no-prompt")
        self.assertEqual(result.returncode, 23)

    def test_custom_entry_and_streamed_execution(self):
        self.archive(name="snapshot/tools/start.sh")
        result = self.run_launcher("--repo", "example/private", "--script", "tools/start.sh", "--no-prompt", streamed=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_stream_does_nothing(self):
        code = self.script.read_text().rsplit("\nfi", 1)[0]
        result = subprocess.run(["/bin/sh", "-s", "--", "--repo", "example/private"], input=code,
                                env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands(), "")

    def test_browser_login_and_prerequisite_prompt_use_terminal(self):
        import fcntl
        import pty
        import termios

        self.env["FAKE_AUTH_STATUS"] = "1"
        (self.bin / "gh").rename(self.bin / "available-gh")
        master, slave = pty.openpty()

        def terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        process = None
        try:
            process = subprocess.Popen(["/bin/sh", "-s", "--", "--repo", "example/private"],
                                       env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, pass_fds=(slave,), preexec_fn=terminal)
            os.write(master, b"y\ny\n")
            output, errors = process.communicate(self.script.read_text(), timeout=10)
            self.assertEqual(process.returncode, 0, errors)
            self.assertIn("browser-login", output)
            self.assertIn("gh auth login --hostname github.com --web --git-protocol https\n", self.commands())
            self.assertIn("Authenticated GitHub account: fixture-user", output)
            self.assertNotIn("gh auth status", self.commands())
            self.assertIn("target-root:", output)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
