# Agent instructions

This is a generic public launcher. Never add infrastructure inventory, hostnames,
SSH public/private keys, credentials, tokens, private repository contents, or
private Git history here. Examples use OWNER/PRIVATE-REPO placeholders.

Keep the entry point POSIX shell and parse streamed input before execution.
Prefer GitHub CLI's supported auth/API behavior over implementing authentication.
Do not print tokens or forward token environment variables to setup scripts.
Keep package installation elevated and all auth/download/setup work non-root.

Update docs and offline tests with behavior changes. Run `sh -n bootstrap.sh`
and `python3 -m unittest discover -s tests -v`. Use small atomic commits.
Never change a target repository's visibility or publish its contents.
