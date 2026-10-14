# PR #137 adversarial review — 2026-10-14

Reviewed initial head `dce4612e80aaead47d2d05b2dcf32ad15505ba2f` against
main `e032c86f1a733f07eba54173e4a351aa432d68d6`.
Scope: full PR, hosted OAuth declaration and user-controlled storage boundaries,
selected-history helper, distribution/source parity, CLI verification and release
failure paths. The primary agent performed this review; it is not represented as
an independent reviewer approval.

## Reproduced findings and corrections

1. **P2 — ZIP output inside the source includes itself or overwrites a source
   file.** `package_cli.py` opened the output before enumerating the subtree.
   A synthetic `--out <source>/release.zip` produced archive members
   `gemini-extension.json`, `GEMINI.md`, `release.zip`.
   Resolved: reject an output within the resolved source before touching it.
   Regression checks cover self-inclusion and replacing a source file.

2. **P2 — rejected inputs or write failures destroy an existing release.**
   Adding a synthetic hidden input caused a prior output to be replaced with a
   22-byte empty ZIP before the source validation raised.
   Resolved: preflight source paths and manifest version, reject linked files
   and directories, write a staging file, and replace the destination only after
   successful archive completion and hashing. Failure removes staging and
   preserves the existing release.
   Regression checks cover hidden input, linked directories, invalid manifest,
   synthetic disk failure and deterministic root-archive contents.

The remote installation verifier also compared only the manifest. Its coverage
now compares every shipped file byte-for-byte and rejects missing or extra
resources, excluding only Gemini's own root installation metadata. Four
regression checks cover valid metadata, changed skills, changed commands and an
unexpected executable resource.

## Validation

- Seven Python packaging regression checks passed on Windows.
- Four Node installed-content regression checks passed.
- Official Gemini CLI 0.64.0, Windows Node 22.23.3: installation from the public
  distribution URL, six-skill discovery, full installed-content equality and
  uninstall passed in an isolated home with MCP excluded.
- Rebuilt CLI ZIP SHA-256 remains
  `1f721b2aa349d248b295520b074ab31ab538f4f75c93fc708ac4dc8b2f7cda6b`.
  The shipped CLI subtree is unchanged; no replacement release or version bump
  is needed for these maintainer tooling fixes.
- CI includes the new failure-path and integrity checks. Merge requires all
  checks on the final reviewed head to succeed.

## Remaining limits

No unresolved blocking finding remains in the reviewed changes. This does not
certify live authenticated OAuth or memory save/recall: those remain explicitly
unverified. The Windows Node 24 native CLI uninstall assertion is an observed
upstream/runtime limitation; Node 22 LTS passed. Consumer Gemini registration
remains unsubmitted, and public CLI gallery visibility remains unconfirmed.

The user explicitly authorized `--admin` merge of PR #137 after adversarial
review and successful checks. This does not change branch protection settings.
