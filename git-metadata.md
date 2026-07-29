# Git Metadata Implementation Plan

## Objective

Extend `source_archive` so `SOURCE-MANIFEST.json` records how the ESPHome configuration and every archived source file relate to the local Git checkout used for the build.

The implementation should preserve two independent kinds of provenance:

1. **Archive integrity** — the existing SHA-256 hash records the exact bytes embedded in the ZIP.
2. **Git provenance** — repository, commit, branch, tracked state, Git status, and committed blob identify the source-control baseline from which those bytes came.

Git metadata must be best-effort. A missing Git executable, a configuration directory outside a repository, an unusual worktree, or a failed Git command must not prevent ESPHome from compiling or serving the source archive.

## Scope

### In scope

- Detect the Git repository containing the active ESPHome YAML.
- Record repository-level Git metadata in the manifest.
- Determine the Git relationship of every entry under `files`.
- Record whether each file is tracked, clean, modified, staged, untracked, ignored, or outside the detected repository.
- Record the committed Git blob SHA for tracked files when available.
- Sanitize repository remotes before writing them into the firmware archive.
- Increment the manifest format version.
- Add automated tests for clean, dirty, staged, untracked, ignored, detached-HEAD, and non-Git cases.
- Document the resulting manifest schema and limitations.

### Not in scope for the first implementation

- GitHub API calls.
- Repository stars, issues, pull requests, releases, or other hosting-service metadata.
- Uploading or synchronizing local changes.
- Embedding `.git` data.
- Reconstructing a patch for modified files.
- Recording credentials from a Git remote.
- Failing compilation when Git metadata cannot be collected.
- Full support for multiple nested repositories or submodules in the first release.

## Design principles

### Best-effort behavior

Git metadata is supplementary. `_build_archive()` must still produce the same valid ZIP when Git is unavailable.

All Git subprocess failures should be handled locally and logged at debug level unless the failure indicates a programming error. The manifest should omit unavailable fields rather than store misleading placeholder values.

### No host-specific paths

Do not store:

- absolute configuration paths,
- repository root paths,
- container mount paths,
- usernames from local filesystem paths.

Store only paths relative to the repository root or paths already used inside the source ZIP.

### No secrets

HTTPS remotes may contain usernames, passwords, or access tokens. Sanitize user information from URL-style remotes before adding them to the manifest.

For example:

```text
https://user:token@github.com/example/config.git
```

must become:

```text
https://github.com/example/config.git
```

SCP-style SSH remotes such as `git@github.com:example/config.git` may be retained because they do not contain an access token, although a later version may normalize them to a canonical host/path form.

### Preserve exact archive hashes

The existing per-file SHA-256 remains authoritative for the bytes actually embedded in the archive. A Git blob SHA describes the version stored in Git and must not replace the SHA-256 field.

## Proposed manifest schema

Increment `format` from `1` to `2`.

### Repository-level metadata

Add an optional top-level `git` object:

```json
{
  "component_version": "1.4.0",
  "configuration": "bedroom-lamp.yaml",
  "esphome_version": "2026.7.3",
  "format": 2,
  "git": {
    "branch": "main",
    "commit": "c7e8d2f0341dd718213c92a451b1fa3b74cd6259",
    "commit_timestamp": "2026-07-29T13:42:17-07:00",
    "configuration_path": "devices/bedroom-lamp.yaml",
    "configuration_tracked": true,
    "describe": "v2.4.1-3-gc7e8d2f",
    "dirty": true,
    "remote": "https://github.com/borisfeldman/esphome.git"
  },
  "files": []
}
```

Recommended repository fields:

- `commit`: full `HEAD` object ID.
- `branch`: current branch name, omitted or `null` for detached HEAD.
- `describe`: result of `git describe --tags --always --dirty`.
- `commit_timestamp`: committer timestamp for `HEAD` in ISO 8601 format.
- `remote`: sanitized `origin` remote, if configured.
- `dirty`: whether the repository has tracked or untracked changes.
- `configuration_path`: active YAML path relative to the repository root.
- `configuration_tracked`: whether the active YAML is tracked by Git.

The absolute repository root must not be included.

### Per-file metadata

Add an optional `git` object to each existing file entry:

```json
{
  "path": "packages/common.yaml",
  "sha256": "16a2cbed194d114a56b14bf8e42775b1ad82b6a34456d99c791a07eafc8d03fa",
  "size": 1312,
  "git": {
    "repository_path": "packages/common.yaml",
    "tracked": true,
    "status": "clean",
    "blob": "6bd2a4d9e5b5a04d6d59c2c4be912d767972ea84"
  }
}
```

Recommended per-file fields:

- `repository_path`: path relative to repository root.
- `tracked`: whether the file exists in the Git index.
- `status`: normalized status value.
- `blob`: blob object ID recorded in `HEAD`, when one exists.
- `index_blob`: optional blob object ID in the index when it differs from `HEAD`.

Initial normalized status vocabulary:

- `clean`
- `modified`
- `staged`
- `modified_and_staged`
- `untracked`
- `ignored`
- `outside_repository`
- `unavailable`

Avoid exposing raw porcelain codes as the only status representation. Raw codes may be useful internally, but the manifest should use stable descriptive values.

## Implementation structure

The first implementation can remain in `components/source_archive/__init__.py`. If Git handling grows significantly, move it into `components/source_archive/git_metadata.py` in a later refactor.

Add the following imports:

```python
import subprocess
from urllib.parse import urlsplit, urlunsplit
```

Consider adding small internal data structures using `TypedDict` or dataclasses, but plain dictionaries are acceptable if consistent with the surrounding component.

## Helper functions

### `_run_git()`

Add a private helper that runs Git against an explicit directory:

```python
def _run_git(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        return None

    return result.stdout.strip() or None
```

Implementation requirements:

- Never invoke a shell.
- Always pass arguments as a list.
- Use an explicit timeout.
- Capture stdout and stderr.
- Return `None` on expected environmental failures.
- Do not log remote URLs before sanitization.

A later refinement may return a result object that distinguishes:

- command unavailable,
- not a Git repository,
- valid command with empty output,
- command failure.

For the first version, `str | None` is adequate where empty output and failure are handled intentionally.

### `_sanitize_git_remote()`

Strip URL credentials while preserving scheme, host, port, path, query, and fragment where safe.

```python
def _sanitize_git_remote(remote: str) -> str:
    if "://" not in remote:
        return remote

    parsed = urlsplit(remote)
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"

    return urlunsplit(
        (
            parsed.scheme,
            hostname,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )
```

Add tests for:

- HTTPS URL without credentials.
- HTTPS URL with username only.
- HTTPS URL with username and token.
- URL with a non-default port.
- SCP-style SSH remote.
- `ssh://` URL with username.

Consider dropping query and fragment fields if there is any chance that a provider uses them for credentials.

### `_find_git_repository()`

Use the active YAML directory as the starting point:

```bash
git -C <config-dir> rev-parse --show-toplevel
```

Return the resolved repository root as a `Path`, or `None` when the active configuration is not in a Git worktree.

Do not manually walk parent directories looking for `.git`, because Git worktrees may use a `.git` file rather than a directory.

### `_repository_relative_path()`

Convert an absolute source path to a POSIX path relative to the repository root.

Requirements:

- Resolve both paths before comparison.
- Catch `ValueError` when the file is outside the repository.
- Never fall back to an absolute path.
- Return `None` for outside-repository files.

### `_collect_repository_git_metadata()`

Collect repository-level metadata once per archive build rather than running the same Git commands for every file.

Suggested commands:

```bash
git -C <repo> rev-parse HEAD
git -C <repo> branch --show-current
git -C <repo> describe --tags --always --dirty
git -C <repo> show -s --format=%cI HEAD
git -C <repo> remote get-url origin
git -C <repo> status --porcelain --untracked-files=normal
```

Determine `configuration_tracked` using:

```bash
git -C <repo> ls-files --error-unmatch -- <configuration-path>
```

Repository `dirty` should be true whenever porcelain status returns any line. This includes untracked files. Document that this is repository-wide, not limited to files included in the archive.

### `_collect_file_git_metadata()`

Inputs:

- repository root,
- source file path,
- optional pre-fetched status map.

Behavior:

1. Determine whether the source is inside the repository.
2. Convert it to a repository-relative POSIX path.
3. Determine whether it is tracked.
4. Determine normalized working-tree/index status.
5. Read the committed blob SHA from `HEAD`, when available.
6. Optionally read the staged blob SHA from the index.

Suggested commands:

```bash
git -C <repo> ls-files --error-unmatch -- <path>
git -C <repo> rev-parse HEAD:<path>
git -C <repo> ls-files --stage -- <path>
git -C <repo> check-ignore -q -- <path>
```

Do not construct `HEAD:<path>` without using the already validated repository-relative path.

## Efficient status collection

Avoid running `git status` separately for every file. Run one repository-wide status command using NUL delimiters:

```bash
git -C <repo> status --porcelain=v1 -z --untracked-files=normal
```

Parse the result into a map keyed by repository-relative path.

NUL-delimited output is important because filenames may contain spaces, tabs, newlines, quotes, or non-ASCII characters.

The parser must handle rename and copy records, which contain two paths. Even though ESPHome YAML filenames are usually simple, the code should not silently misparse valid Git output.

For the first release, an acceptable simpler approach is to invoke:

```bash
git -C <repo> status --porcelain=v1 -- <path>
```

for each archived file, because source archives normally contain few files. If this simpler approach is used, document the performance tradeoff and add a follow-up item to batch status collection.

## Status normalization

Git porcelain uses two status columns:

- first column: index status,
- second column: working-tree status.

Normalize as follows:

| Git condition | Manifest status |
|---|---|
| no output and tracked | `clean` |
| `??` | `untracked` |
| ignored according to `check-ignore` | `ignored` |
| index changed, worktree clean | `staged` |
| index clean, worktree changed | `modified` |
| both columns changed | `modified_and_staged` |
| path outside repository | `outside_repository` |
| metadata command failed unexpectedly | `unavailable` |

Renames, copies, type changes, conflicts, and deletions require decisions:

- A source file that exists and is archived after a rename may be treated as `staged` or `modified_and_staged`, depending on the worktree column.
- A deleted file cannot normally be archived because `_build_archive()` reads it before metadata generation.
- Conflicted files should map to `modified_and_staged` initially, or gain a future `conflicted` status.
- Type changes can map to `modified` or `staged` according to the column in which they occur.

Document any lossy normalization.

## Blob metadata

### `blob`

For a tracked file present in `HEAD`, collect:

```bash
git -C <repo> rev-parse HEAD:<repository-path>
```

This records the committed baseline.

A newly added staged file may be tracked in the index but absent from `HEAD`; omit `blob` in that case.

### `index_blob`

Optionally collect the staged blob using:

```bash
git -C <repo> ls-files --stage -- <repository-path>
```

Store `index_blob` when:

- the file is staged,
- the index blob exists,
- and it differs from the `HEAD` blob.

This gives three useful content identities:

- `sha256`: bytes archived from the working tree,
- `index_blob`: bytes staged in the index,
- `blob`: bytes committed in `HEAD`.

For a clean file, `index_blob` adds little value and should be omitted.

## Changes to `_build_archive()`

Current processing creates `manifest_files` while reading each file. Extend this flow:

1. Build `source_files` as today.
2. Detect repository metadata once using `CORE.config_dir`.
3. Collect or initialize the Git status map.
4. For each source file:
   - read file bytes,
   - calculate SHA-256,
   - determine size,
   - collect per-file Git metadata,
   - add the optional `git` object.
5. Build a mutable `manifest_data` dictionary.
6. Add the top-level `git` object only when repository detection succeeds.
7. Serialize with the existing deterministic `indent=2` and `sort_keys=True` behavior.

Suggested shape:

```python
manifest_data = {
    "component_version": VERSION,
    "configuration": Path(CORE.config_path).name,
    "esphome_version": ESPHOME_VERSION,
    "files": manifest_files,
    "format": 2,
}

if repository_metadata is not None:
    manifest_data["git"] = repository_metadata
```

Keep the trailing newline after JSON serialization.

## Nested repositories and submodules

The initial implementation should define a clear rule:

- The top-level `git` object describes the repository containing the active ESPHome YAML.
- A file is considered part of that repository only when its resolved path is beneath that repository root.
- Files within nested repositories or submodules may initially be reported as belonging to the main repository path if Git treats the submodule directory as a tracked gitlink, but their internal file metadata will not be resolvable from the parent repository.

For those files, use one of these initial behaviors:

1. Mark `status` as `unavailable` and omit blob metadata, or
2. Detect the nested repository with `git -C <file-parent> rev-parse --show-toplevel` and omit per-file Git metadata when it differs from the configuration repository.

The second behavior is preferred because it avoids presenting incorrect parent-repository information.

A future format may add a top-level `repositories` array with stable local IDs and allow each file to reference its own repository.

## Logging

Add debug logging for metadata detection, for example:

- Git executable unavailable.
- Configuration directory is not in a Git repository.
- Remote omitted because it could not be sanitized.
- Per-file Git metadata unavailable.

Do not log:

- unsanitized remotes,
- complete environment variables,
- absolute filesystem paths at info level,
- file contents.

The existing info message for archive generation should remain concise.

## Error handling

Git metadata failures must not abort compilation.

Cases to handle:

- `git` executable missing.
- Command timeout.
- Configuration directory not in a repository.
- Repository with no commits yet (`HEAD` is unborn).
- Detached HEAD.
- Missing `origin` remote.
- File outside repository.
- Tracked file absent from `HEAD` but present in index.
- Untracked file.
- Ignored file explicitly included in the archive.
- Worktree using a `.git` file.
- Paths containing spaces or Unicode.
- Permission errors reading Git metadata.

Only ordinary file-reading failures for archive source files should continue to use the existing ESPHome validation/build failure behavior.

## Security review

Before release, verify:

- No subprocess call uses `shell=True`.
- Every Git path argument follows `--` where applicable.
- No user-controlled value is interpolated into a shell command.
- URL credentials are removed.
- Absolute host paths are not serialized.
- Git metadata cannot cause `secrets.yaml` to be embedded.
- Git commands do not follow arbitrary hooks or execute repository scripts. The proposed read-only commands do not invoke hooks.
- Timeouts prevent a hung Git process from blocking compilation indefinitely.

## Test plan

Add tests around pure parsing and sanitization helpers, plus temporary-repository integration tests.

### Unit tests

Test `_sanitize_git_remote()` with:

- ordinary GitHub HTTPS remote,
- HTTPS remote containing username and token,
- `ssh://` remote,
- SCP-style remote,
- remote with port,
- malformed remote.

Test status normalization with:

- clean,
- modified,
- staged,
- modified and staged,
- untracked,
- ignored,
- rename,
- conflict porcelain codes.

Test repository-relative path conversion with:

- file at repository root,
- nested file,
- file outside repository,
- paths containing spaces,
- Unicode filename.

### Integration tests

Create temporary Git repositories and verify manifest output for:

1. Clean tracked active YAML.
2. Modified tracked active YAML.
3. Staged active YAML.
4. File modified after staging.
5. Newly staged file absent from `HEAD`.
6. Untracked included file.
7. Ignored included file.
8. Detached HEAD.
9. Repository without an `origin` remote.
10. Configuration directory outside Git.
11. Git executable unavailable or mocked failure.
12. Repository with no commits.
13. Included file outside the repository but inside ESPHome’s allowed config directory.
14. Included file in a nested Git repository.

Assertions should verify:

- compilation/archive creation still succeeds,
- existing SHA-256 and size values remain unchanged,
- Git fields appear only when available,
- no absolute paths appear,
- remotes contain no credentials,
- manifest JSON ordering remains deterministic,
- repeated builds from the same state produce byte-identical manifests and ZIP archives.

## Compatibility and versioning

- Increment manifest `format` to `2` because the schema gains new optional objects.
- Keep existing top-level fields unchanged.
- Keep existing file fields unchanged.
- Make all Git fields optional so older consumers can ignore them and non-Git builds remain valid.
- Update the component version and Git tag together according to the existing release convention.
- Update README examples to show both Git and non-Git manifests.

A consumer should treat an omitted `git` object as “Git metadata was not available,” not necessarily “the file was not tracked.”

## Documentation updates

Update `README.md` with:

- A description of repository and per-file Git provenance.
- A full format-2 manifest example.
- Explanation of `sha256` versus Git `blob`.
- Explanation of `dirty` as repository-wide state.
- Explanation of staged versus working-tree content.
- Privacy behavior for paths and remotes.
- Best-effort behavior when Git is unavailable.
- Limitations involving submodules and nested repositories.

## Recommended implementation phases

### Phase 1: Repository metadata

- Add `_run_git()`.
- Add remote sanitization.
- Detect repository root.
- Add commit, branch, timestamp, describe, remote, dirty, configuration path, and tracked state.
- Increment manifest format.
- Add core repository-level tests.

### Phase 2: Per-file metadata

- Add repository-relative paths.
- Add tracked state and normalized status.
- Add committed blob SHA.
- Add tests for clean, modified, staged, and untracked files.

### Phase 3: Index and edge cases

- Add `index_blob` for staged content.
- Improve NUL-delimited porcelain parsing.
- Handle renames and conflicts explicitly.
- Add nested-repository and submodule behavior.
- Expand documentation.

### Phase 4: Optional schema refinement

Evaluate whether multiple repositories should be represented as:

```json
{
  "repositories": [
    {
      "id": "config",
      "commit": "...",
      "remote": "..."
    }
  ]
}
```

with each file referencing a repository ID. Do not add this complexity until a real use case requires it.

## Acceptance criteria

The implementation is complete when:

- A clean tracked YAML produces repository metadata and clean per-file metadata.
- A modified tracked YAML records the `HEAD` commit and blob while its SHA-256 reflects current disk contents.
- A staged file can distinguish committed, staged, and archived content where applicable.
- Untracked and ignored files are represented accurately.
- Builds outside Git produce a valid format-2 manifest without a top-level `git` object.
- Git failures never prevent archive generation.
- Remote credentials and absolute filesystem paths never appear in the manifest.
- Tests cover the principal states and deterministic output.
- README documentation explains the schema and limitations.
