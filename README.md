# Source Archive Component

The `source_archive` component embeds the configuration a device was built from into the
firmware itself and serves it back as a single ZIP archive over the
[Web Server](https://esphome.io/components/web_server/).

A node flashed a year ago can then tell you exactly what produced the binary it is
running, without you having to guess which revision of which YAML file was on your laptop
at the time. The archive is assembled at compile time and stored in flash, so it always
matches the running firmware.

> [!NOTE]
> Requires ESPHome 2026.2.0 or newer, and the
> [Web Server](https://esphome.io/components/web_server/) component.

> [!WARNING]
> The archive is served to anyone who can reach the device. Read
> [Keeping the archive private](#keeping-the-archive-private) before enabling it on a node
> whose configuration holds credentials.

```yaml
# Example configuration entry
external_components:
  - source: github://borisfeldman/source-archive@v1.4.0
    components: [source_archive]
    refresh: never

web_server:

source_archive:
  filename: bedroom-lamp-source.zip
  files:
    - packages/wifi.yaml
    - packages/sensors.yaml
```

Browsing to `http://bedroom-lamp.local/source.zip` now downloads `bedroom-lamp-source.zip`,
containing `bedroom-lamp.yaml`, both packages and a manifest.

## Configuration variables

- **files** (*Optional*, list of files): Additional files to embed in the firmware. Paths
  are relative to the configuration directory and are kept as-is inside the archive.
- **include_current_config** (*Optional*, boolean): Also embed the YAML file being
  compiled. Defaults to `true`.
- **include_git_metadata** (*Optional*, boolean): Record the Git commit and per-file
  provenance for a configuration kept in a repository. See
  [Git provenance](#git-provenance). Defaults to `true`.
- **download_path** (*Optional*, string): The URL path the archive is served from. Must be
  absolute, must not contain a query string or fragment, and must not be one of the paths
  `web_server` already answers (`/`, `/0.css`, `/0.js`, `/events`). Defaults to
  `/source.zip`.
- **filename** (*Optional*, string): The name the browser saves the download as. Must end
  in `.zip` and use only letters, digits, dots, dashes and underscores. Defaults to
  `esphome-source.zip`.
- **manifest_only** (*Optional*, boolean): Embed only `SOURCE-MANIFEST.json` and leave the
  file contents out of the archive. See
  [Shipping only the manifest](#shipping-only-the-manifest). Defaults to `false`.
- **max_size** (*Optional*, bytes): Refuse to build if the compressed archive grows beyond
  this size. Defaults to `64kB`.
- **download_button** (*Optional*, boolean): Add a **Download source** button to the device
  web interface. Defaults to `false`.
- **id** (*Optional*, [ID](https://esphome.io/guides/configuration-types#id)): Manually
  specify the ID used for code generation.

> [!NOTE]
> Packages pulled in with `!include` are not discovered automatically. ESPHome resolves
> them into a single configuration long before this component runs, so every file you want
> in the archive has to be named in `files:`.

A device built from a single YAML file needs no `files:` at all — the configuration being
compiled is archived on its own:

```yaml
source_archive:
```

Something has to end up in the archive, so setting `include_current_config: false` without
listing any `files:` is rejected.

Paths must stay inside the configuration directory: absolute paths and `..` are rejected,
and every file has to exist when the configuration is validated. `.esphome/` is rejected
too, so the component sources `external_components` downloads and the rest of ESPHome's
working state stay out of the archive. Duplicates are dropped, so naming the current
configuration file explicitly alongside `include_current_config: true` is harmless.

## Keeping the archive private

A device page has no login by default, and neither does the archive. Anything that can
reach the device can read your configuration, so give `web_server` credentials before
enabling the component on a node that holds any:

```yaml
web_server:
  auth:
    username: !secret web_username
    password: !secret web_password
```

The archive inherits this automatically — `web_server_base` wraps every handler registered
with it in the same authentication middleware, so there is no separate switch here. Note
that `web_server` speaks plain HTTP, so credentials are only as private as the network
they cross.

What gets embedded is the YAML as you wrote it, not as ESPHome resolved it: `!secret`
references are still unresolved references in the archive, and `secrets.yaml` is refused
outright if you try to list it. Credentials written inline are the real hazard. A literal
`password:` under `wifi:`, an `api:` encryption key, an `ota:` password or MQTT
credentials are copied in verbatim and handed to whoever asks. Move them into
`secrets.yaml` first.

A configuration that lives in Git has a third option:
[manifest_only](#shipping-only-the-manifest) serves the digests and the commit the build
came from without serving a byte of the configuration itself.

## Archive contents

Entries keep the relative paths you gave them, and a manifest is added last:

```
bedroom-lamp-source.zip
├── bedroom-lamp.yaml
├── packages/wifi.yaml
├── packages/sensors.yaml
└── SOURCE-MANIFEST.json
```

With [manifest_only](#shipping-only-the-manifest), the last entry is the only one.

`SOURCE-MANIFEST.json` records the ESPHome version used for the build, a SHA-256 digest
for every entry, and — when the configuration lives in a Git repository — the commit it was
built from:

```json
{
  "component_version": "1.4.0",
  "configuration": "bedroom-lamp.yaml",
  "esphome_version": "2026.7.3",
  "files": [
    {
      "git": {
        "blob": "9b5bf1ae…",
        "repository": "config",
        "repository_path": "devices/bedroom-lamp.yaml",
        "status": "clean",
        "tracked": true
      },
      "path": "bedroom-lamp.yaml",
      "sha256": "3b1f0c9e…",
      "size": 1042
    }
  ],
  "format": 2,
  "git": {
    "branch": "main",
    "commit": "c7e8d2f0341dd718213c92a451b1fa3b74cd6259",
    "commit_timestamp": "2026-07-29T13:42:17-07:00",
    "configuration_path": "devices/bedroom-lamp.yaml",
    "configuration_tracked": true,
    "describe": "v2.4.1-3-gc7e8d2f",
    "dirty": false,
    "remote": "https://github.com/borisfeldman/esphome.git"
  }
}
```

Entries are written with a fixed timestamp and fixed permissions, so identical inputs
always produce a byte-identical archive.

## Git provenance

A SHA-256 tells you *whether* your files still match the firmware. Git tells you *which
revision* they were, which is the thing you actually want a year later. If the
configuration being compiled sits in a Git repository, the manifest records that repository
alongside the digests. Nothing needs configuring and nothing is contacted over the network —
everything comes from `git` commands run against the local checkout at compile time, and
`include_git_metadata: false` turns the whole thing off.

The top-level `git` object describes the repository holding the configuration:

- **commit** — the full `HEAD` SHA.
- **branch** — the current branch. Omitted on a detached HEAD.
- **describe** — `git describe --tags --always --dirty`, so a tagged build reads
  `v2.4.1` rather than a bare SHA.
- **commit_timestamp** — the committer date of `HEAD`, in ISO 8601.
- **remote** — the `origin` URL, with any credentials stripped.
- **configuration_path** — the compiled YAML's path relative to the repository root.
- **configuration_tracked** — whether Git tracks that YAML at all.
- **dirty** — whether the repository had staged, unstaged or untracked changes.

Each entry under `files` carries its own `git` object with a `repository_path`, a `tracked`
flag, a `status`, and the `blob` Git holds for it in `HEAD`. `status` is one of:

| Status | Meaning |
|---|---|
| `clean` | The file on disk matches `HEAD`. |
| `modified` | Edited in the working tree and not staged. |
| `staged` | Staged, with no further edits on disk. |
| `modified_and_staged` | Staged, then edited again. |
| `untracked` | Inside the repository, but not known to Git. |
| `ignored` | Matched by `.gitignore`. Still archived if you listed it. |
| `outside_repository` | Not part of the configuration's repository. |
| `unavailable` | The repository is known but Git could not answer for this file. |

### Digest or blob

The two hashes answer different questions, and a file that was edited but not committed is
where the difference shows:

- **sha256** is the archive's own digest of the exact bytes that were compiled into the
  firmware. It is always authoritative for what the device is running.
- **git.blob** is the object ID of the *committed* version in `HEAD` — the baseline the
  build drifted from, and what `git show <blob>` hands back.
- **status** explains why the two describe different content.

They are not comparable values: Git hashes an object header along with the content, and a
repository may use SHA-1 or SHA-256 objects. Treat `blob` as an identifier to look up, not
a checksum to verify.

```bash
unzip -p bedroom-lamp-source.zip SOURCE-MANIFEST.json | jq -r '.git.commit'
git show "$(unzip -p bedroom-lamp-source.zip SOURCE-MANIFEST.json \
  | jq -r '.files[0].git.blob')"
```

### When Git is not there

Git metadata is provenance, not a build input, and it is collected strictly best-effort.
No Git, no repository, a `.git` directory that was never mounted into the container, a
checkout with no commits, no `origin` remote, an unreadable repository, a Git call that
hangs — each one is answered by leaving fields out, never by failing the build. A
configuration outside a repository simply produces a manifest with no `git` objects, exactly
as format 1 did.

The same is available on request. `include_git_metadata: false` skips the collection
altogether — no `git` runs at compile time, and the manifest carries only the fields
format 1 defined:

```yaml
source_archive:
  include_git_metadata: false
```

The manifest still declares `"format": 2`, which versions the layout the component can
produce rather than what any one build chose to fill in.

Files pulled in from a *different* repository — a submodule, or a nested checkout — are
recorded as `outside_repository` rather than described against a repository the manifest
does not name.

Credentials never reach the archive. A remote such as
`https://user:ghp_token@github.com/owner/repo.git` is written as
`https://github.com/owner/repo.git`, and query strings and fragments go with it. Neither
does anything about your machine: the manifest stores repository-relative paths only, so
absolute paths, home directories and container mount points stay out of it, and Git's
stderr is never logged or recorded.

### Shipping only the manifest

Once the manifest names a commit and a blob per file, the firmware is carrying a second
copy of something the repository already holds. `manifest_only` drops that copy:

```yaml
source_archive:
  manifest_only: true
  files:
    - packages/wifi.yaml
```

The archive is then a single `SOURCE-MANIFEST.json` — no file entries at all — and the
manifest sets `"manifest_only": true` so a reader knows the entries are absent by design
rather than truncated. Everything describing the build survives: the file list, sizes,
SHA-256 digests, the commit, and each file's `blob` and `status`. Recovery moves from
`unzip` to `git`:

```bash
curl -OJ http://bedroom-lamp.local/source.zip
git -C ~/esphome checkout "$(unzip -p bedroom-lamp-source.zip SOURCE-MANIFEST.json \
  | jq -r '.git.commit')"
```

The trade is real. A manifest costs a few hundred bytes of flash where the files cost
kilobytes, and the device stops serving your configuration to anyone who asks for it — but
it can no longer tell you what the configuration *was* on its own. That only works for
files the repository can still produce, so check the recorded statuses before relying on
it: a file marked `modified`, `untracked` or `ignored` was never committed in the form that
was compiled, and those exact bytes exist nowhere but the machine that built the firmware.
The SHA-256 digests will tell you when what you recovered is not what was flashed.

The option leans entirely on the Git data, so combining it with
`include_git_metadata: false` leaves names, sizes and digests and nothing to recover
from — a record for checking configurations against, not an archive to restore from.

## Downloading the archive

```bash
curl -OJ http://bedroom-lamp.local/source.zip
unzip -l bedroom-lamp-source.zip
```

The response is sent as `application/zip` with `Content-Disposition: attachment`,
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.

The manifest digests are for spotting drift rather than for checking the download — `unzip`
already verifies a CRC-32 per entry. Run them from your configuration directory to see
whether what is on disk still matches what the device is running:

```bash
cd ~/esphome
unzip -p bedroom-lamp-source.zip SOURCE-MANIFEST.json \
  | jq -r '.files[] | "\(.sha256)  \(.path)"' \
  | shasum -a 256 -c
```

```
bedroom-lamp.yaml: OK
packages/wifi.yaml: FAILED
packages/sensors.yaml: OK
```

Use `sha256sum -c` in place of `shasum -a 256 -c` on Linux.

## Adding a download button to the web interface

Set `download_button`, and a floating **Download source** button appears on the device page:

```yaml
web_server:

source_archive:
  download_button: true
  files:
    - packages/wifi.yaml
```

Nothing needs copying into your configuration directory. The component generates the button
script with your `download_path` already substituted, writes it into ESPHome's internal data
directory, and points `web_server`'s `js_include` at it during final validation. Change
`download_path` and the button follows.

ESPHome serves the script as `/0.js` alongside the regular web interface script, so the
standard UI keeps working.

The button fetches the archive and checks the response before saving it. If the device
answers with an error it turns red and reports **Source unavailable** rather than saving the
error response to disk; the reason is in the `title` attribute and the browser console.

![The Download source button pinned to the corner of the device web interface, below the OTA Update section](images/download-source-button.png)

### Wiring the script up yourself

`web_server` accepts exactly one `js_include`. If you already use it for your own script,
`download_button` fails validation rather than overwriting it — merge the button into your
script instead, starting from
[source_archive_link.js](components/source_archive/source_archive_link.js).

The same applies if you would rather manage the file yourself. Copy it next to your
configuration and point `js_include` at it, leaving `download_button` off:

```yaml
web_server:
  js_include: source_archive_link.js
```

`js_include` resolves relative to your configuration directory and cannot reach the script
that `external_components` downloads — that lives under ESPHome's data directory, in a
folder named after a hash of the repository URL and ref, so the path changes whenever you
bump the pin. Fetch a copy from the tag instead:

```bash
cd /config
curl -fsSLo source_archive_link.js \
  https://raw.githubusercontent.com/borisfeldman/source-archive/v1.4.0/components/source_archive/source_archive_link.js
```

Pointing `js_include` at a path that is not there fails validation before anything else
runs:

```
Could not find file '/config/components/source_archive/source_archive_link.js'.
```

On this route the path is yours to maintain: `DOWNLOAD_PATH` at the top of the script has to
match `download_path` by hand.

> [!IMPORTANT]
> A hand-wired `js_include` is independent of `source_archive:`. Including the script without
> configuring the component leaves a button on the page with nothing behind it: no
> `/source.zip` route is registered, so the request falls through to the web server's 404.

## Versioning

Releases are tagged `vMAJOR.MINOR.PATCH`. The public surface is the YAML schema and the
archive layout:

- **MAJOR** — a configuration that used to validate no longer does: an option renamed or
  removed, a new required option, or a changed default that alters what lands in the
  archive.
- **MINOR** — new optional options, or additions to `SOURCE-MANIFEST.json`.
- **PATCH** — fixes that leave both the schema and the archive layout alone.

Pin to a tag, and set `refresh: never` so ESPHome stops re-checking the repository:

```yaml
external_components:
  - source: github://borisfeldman/source-archive@v1.4.0
    components: [source_archive]
    refresh: never
```

This matters more here than for most components. An unpinned source tracks the default
branch and refreshes daily, so a configuration recovered from an archive may fail to
validate against a later release — the archive would preserve your YAML perfectly and still
leave you unable to rebuild it. Because the archive stores that YAML verbatim, a pin
travels inside it: the recovered configuration asks for the exact component version that
built the firmware.

The component's own source cannot be archived — `external_components` places it under
`.esphome/`, outside the configuration directory, where [files](#configuration-variables)
is not allowed to reach. `SOURCE-MANIFEST.json` records the version instead, which is what
tells you which tag to pin if the configuration never was:

```json
{
  "component_version": "1.4.0",
  "esphome_version": "2026.7.3",
  "format": 2
}
```

`format` versions the manifest layout itself, independently of the component release.
Format 2 added the optional `git` objects and the optional `manifest_only` flag; every
field format 1 defined kept its meaning, so a reader that ignores fields it does not
recognise handles both.

## Logs

ESPHome reports what went into the firmware while compiling:

```
INFO source_archive 1.4.0 embedded 3 files in bedroom-lamp-source.zip (2847 bytes)
```

Under `manifest_only` the files are described rather than embedded, and the line says so:

```
INFO source_archive 1.4.0 listed 3 files in bedroom-lamp-source.zip (631 bytes)
```

and the device repeats it on boot:

```
[C][source_archive:060]: Source Archive:
[C][source_archive:061]:   Download path: /source.zip
[C][source_archive:062]:   Filename: bedroom-lamp-source.zip
[C][source_archive:063]:   Size: 2847 bytes
```

## Flash usage

The archive is stored as a constant byte array in flash, so it costs exactly the compressed
size reported above. Entries are deflated at maximum compression; YAML packs down well and
even a large configuration with several packages lands in single-digit kilobytes —
negligible next to the several hundred kilobytes of an ESPHome binary, and not something
that needs weighing against other features.

The `max_size` limit exists to catch the case where that stops being true — a font, an
image directory or a stray binary picked up by a broad `files:` list. The default of `64kB`
is roughly ten times what a large YAML-only configuration produces, and small enough to
matter on a 1 MB ESP8266 where an OTA slot leaves little headroom. Going over it fails the
build:

```
Source archive is 91204 bytes, over the 64000 byte 'max_size' limit. Drop entries
from 'files', or raise 'max_size' if the firmware has room for it.
```

Raise it deliberately when the space is genuinely there:

```yaml
source_archive:
  max_size: 128kB
  files:
    - packages/wifi.yaml
```

Where the space is not there — a nearly full ESP8266, or a `files:` list that is large by
necessity — [manifest_only](#shipping-only-the-manifest) trades the contents for a few
hundred bytes of digests and Git provenance instead.

## Security

> [!WARNING]
> The archive is served by `web_server`, which is unauthenticated by default. Anyone who
> can reach the device can download everything listed in `files:`.

- Listing `secrets.yaml` is rejected at validation time. Take the same care with any other
  file holding credentials, API keys or certificates — those are not detectable.
- Files are embedded byte for byte as they are on disk. `!secret` references and
  substitutions are *not* resolved, so the archive contains `!secret wifi_password` rather
  than the password itself — provided the secrets file is not in the list.
- [Git provenance](#git-provenance) records a sanitized `origin` URL: usernames, passwords
  and access tokens are stripped, and no absolute path from the build machine is written to
  the manifest. The repository URL itself is part of the archive, so a private remote's
  address is readable by anyone who can download it.
- [manifest_only](#shipping-only-the-manifest) leaves the file contents out of the
  firmware entirely, so an unauthenticated download exposes filenames, sizes, digests and
  Git provenance rather than the configuration.
- `web_server` sends `Access-Control-Allow-Origin: *`, so a web page you visit while on the
  same network can read the archive cross-origin. This is
  [intentional in ESPHome](https://github.com/esphome/esphome/blob/dev/THREAT_MODEL.md) and
  applies to the whole web interface, but the archive makes your configuration part of what
  is reachable that way.
- Enable authentication if the device is on a network you do not fully trust. The download
  endpoint is registered through `add_handler()`, so it sits behind the same auth
  middleware as the rest of the web interface:

  ```yaml
  web_server:
    auth:
      username: !secret web_server_username
      password: !secret web_server_password
  ```

See [Security Best Practices](https://esphome.io/guides/security_best_practices/).

## See Also

- [sample.yaml](sample.yaml) — minimal configuration using this component
- [Web Server Component](https://esphome.io/components/web_server/)
- [External Components](https://esphome.io/components/external_components/)
