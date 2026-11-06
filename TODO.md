# TODO

Open issues and ideas, roughly in priority order. See [`ARCH.md`](ARCH.md) for where each change belongs.

## Dependencies

The groundwork exists: `domain/requirements.py` models the requirement graph among installed mods (`DependencyGraph`: `dependencies`, `dependents`, `not_installed`, with unknown requirements kept apart from known ones), `app/requirements.py` loads it from Nexus, and `UpdateSession` can hold it. Nothing drives the load or shows it yet; `Sync` should own that (the load is blocking, so off the UI thread, and it is optional data that must never stop or degrade the scan). Two features want it, and they are the nearest-term work here:

- [ ] **Warn when a mod is updated without its dependencies.** If the user downloads or installs an update for a mod whose installed dependencies also have updates on offer and not selected or downloaded, say so before they proceed (and name the mods), rather than silently updating one side. Decide where the warning lives (the confirmation prompt, a row marker, or the downloads line), and how to treat requirements the graph marks incomplete or unknown (probably as "no warning", never as "no dependencies").
- [ ] **Let users inspect dependencies and dependants.** Show, for a selected mod, the installed mods it requires and the installed mods that require it, including requirements that aren't installed. The notes pane or a row tooltip are candidates.

## Verification

- [ ] **Confirm `IPluginGame.nexusGameID()`** returns the numeric v2 game ID (1704 for Skyrim SE). The adapter resolves the ID from the game's domain name when this returns 0, but a wrong non-zero value would not be detected.

## Download matching

- [ ] **`.meta` timing is a guess.** The tracker re-reads an archive's `.meta` for about 1.75 s, assuming MO2 sometimes reports completion before writing `fileID`. Observe MO2 2.5.2's actual timing and adjust `DEFAULT_RETRY_DELAYS_MS` in `tracking/tracker.py`.
- [ ] **Duplicate-download naming is a guess.** `nexus/archives.py` ignores a trailing `(1)`-style suffix. Confirm how MO2 names a second download of the same file.

## Nexus v2 API (beta)

- [ ] **Survive a dropped `sqid` or `uri`.** If v2 removes either, the query fails validation and the scan stops as "incompatible". Retry without them for the rest of the session, as `nexus/catalog.py` already does for the note fields, so only name-based download matching is lost.
- [ ] **v1 REST fallback source.** Add another `ModFileSource` adapter for `/v1/games/{domain}/mods/{id}/files.json`, used when v2 is unavailable or incompatible. v1 also has a `file_updates` chain (old file → new file), which could resolve cases v2's `groupId` can't. It requires a Nexus API key, which MO2's SDK doesn't expose, so the plugin would need a setting for one.
- [ ] **Respect rate-limit headers.** An HTTP 429 currently counts as "unavailable" (the scan stops after 3 in a row). Read the rate-limit headers, or back off and retry, instead.

## File selection

- [ ] **File-group lineage breaks** when an author moves a file line to a new group (seen in Actor Value Generator's history). These cases fall back to the primary MAIN file as **Needs review**. Possible improvements: v1's `file_updates` chain, or name or category similarity between the old and new groups.
- [ ] **Mods with no recorded file ID** (no `[installedFiles]` and no `.meta` for the installation archive) can only get a **Needs review** suggestion. Options:
  - Read the `sqid` from a new-style `installationFile` name and look it up in the mod's file list.
  - Use v2 `fileHash(md5)` on the original archive, if it's still in Downloads.

## MO2 integration limits

- [ ] **Mods from another Nexus game** (e.g. Skyrim LE mods in an SSE instance) are skipped, because `startDownloadNexusFile` only works for the managed game. A possible workaround is handing an `nxm://<domain>/mods/<mod>/files/<file>` link to MO2. That's untested, and Premium downloads may still need a key.
- [ ] **Installs aren't hands-off.** `installMod` shows MO2's Merge/Replace/Rename prompt and any FOMOD wizard, and the SDK can't suppress them. Consider guidance in the UI, or reusing previous FOMOD choices if MO2 ever exposes that.
- [ ] **No "update check finished" event.** The plugin relies on MO2's `newestVersion` flags, which can be stale. `scan_all_nexus_mods` works around this but is slower. Revisit if the SDK gains a hook.
- [ ] **Non-Premium accounts** are not supported, because direct downloads need Premium. At minimum, detect this and say so instead of letting downloads fail.

## Download progress

MO2 reports no progress for downloads (`IDownloadManager` in the 2.5.2 stubs has no progress, size or query-in-flight; the 2.5.3 beta stubs weren't checked), so the UI shows a stage per row (icon) and a "N/M finished" line, not a percentage. We will eventually want a better way to communicate progress. These are the constraints on any revision, so a design starts from them:

- [ ] **Per-row percentages need data MO2 doesn't give.** The only route found is polling MO2's partial download file on disk against the file's size from Nexus. That needs a new `host` filesystem port, a new field from the beta v2 API (no file size is fetched today), and matching partial files to rows by guessed naming rules (which `nexus/archives.py` already admits are guesses). Judged fragile; revisit if MO2 grows a progress API (check the 2.5.3 stubs first).
- [ ] **Qt has no circular progress widget.** `QProgressBar` is linear and its busy mode doesn't animate inside a cell, and `setIndexWidget` scales poorly and fights the filter proxy. That is why `ui/status_delegate.py` draws the spinner itself with `QPainter.drawArc` as the status cell's decoration. A percentage in that cell would be a delegate drawing a sweep angle (or a bar), not a widget; any new indicator should stay a delegate.
- [ ] **A time-based release is a stand-in, not progress.** `DOWNLOAD_PATIENCE_MS` frees an unmatched download after 30 minutes only because there is nothing better to go on; real progress could replace it with a proper stalled/slow distinction.

## Observations outside the plugin

- Nexus's newer archive names (`<name> <modid> <version> <date> <sqid>.<ext>`) probably aren't recognised by MO2 2.5.2's own filename parsing, so manually added archives without a `.meta` may not get a mod ID. This is inferred, not tested. The bundled `nexus_mod_id_fixer` plugin already recognises the new format.

## Code structure

- [ ] **Reify `domain.Selection`.** `Selection.target` is optional, and `None` exactly when the verdict is `UP_TO_DATE` or `UNRESOLVED`, so a `READY` or `REVIEW` selection with no target is representable. `app/session.py` converts it once (`_finding`, with an `assert` that the target exists), which contains the problem but doesn't remove it. Replace `Selection` with a union per verdict (for example `Offered(target, needs_review, …)`, `UpToDate`, `Unresolved`) and let the session pass it through. This touches `domain/selection.py`, `app/session.py` and the many `test_domain_selection` cases, for consistency only; there is no known bug.
