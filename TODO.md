# TODO

Open issues and ideas, roughly in priority order. See [`ARCH.md`](ARCH.md) for where each change belongs.

## Verification

- [ ] **Run a download and install in MO2 end to end.** Check: the progress bar hides after the scan and after downloads, finished downloads are matched to their rows, and installing works.
- [ ] **Add a permanent test suite** (e.g. `tests/`, runnable outside MO2 with stand-in `mobase`/Qt modules). Cover: the dependency rules between layers, the successor policy including incomplete data, error classification using the live API's response shapes, the scan's early-stop rules, the tracker's matching rules and retries, session lifecycle, and the lock and notes cases below.
- [ ] **Test the download queue (`max_concurrent_downloads`).** Cover `UpdateSession` with a fake `Downloader` and the real `DownloadTracker`: never more than the limit in flight, a limit of 1 runs strictly in order, both a success and a failure start the next queued entry, `close()` clears the queue, and a re-queued row can't be queued twice. Also check in MO2 that the dialog's progress bar counts `Queued` rows as unfinished.
- [ ] **Test version locks.** Cover:
  - `UpdateSession` with a fake `ModLocks` and `Downloader` and the real `DownloadTracker`:
    - a mod locked before the scan loads locked, and `download()` refuses it;
    - a lock covers every entry of the mod (several installed files) and no other mod;
    - locking a `QUEUED` entry dequeues it and sets it back to `IDLE`;
    - locking a `DOWNLOADING` entry leaves it in flight but blocks `install()` once it is `DOWNLOADED`;
    - unlocking makes it downloadable again without starting anything;
    - a dequeued entry doesn't hold a download slot;
    - setting the state a mod already has notifies no one.
  - `Mo2Host` lock storage with a fake organizer:
    - the stored value round-trips;
    - malformed or empty stored values read as "nothing locked";
    - unlocking one mod keeps the others.
  - In MO2:
    - toggling one row's lock updates every row of that mod. Needs several rows for one Nexus mod, which the current test instance doesn't have: either one MO2 mod installed from several Nexus files, or two MO2 mods from the same Nexus mod (e.g. an optional file installed as its own mod).
- [ ] **Test the notes pane.** Cover:
  - `select_successor`'s `newer_files`: newest first, includes the target, empty when up to date, `(candidate,)` for the primary-file fallback.
  - `nexus/catalog.py`: note fields parsed defensively; a schema error drops them for the session and retries once; `mod_description` on a missing mod raises `SourceError`.
  - `markup_to_html`: script, `javascript:` and attribute-breaking URLs are neutralised; links, styles and line breaks survive.
  - `NotesPane`: a failed mod-page load is retried when the row is selected again, a successful one is not, and only the latest selection is fetched while a load is in flight. Use a fake `ModNotes` that fails first and then succeeds.
- [ ] **Confirm `IPluginGame.nexusGameID()`** returns the numeric v2 game ID (1704 for Skyrim SE). The adapter resolves the ID from the game's domain name when this returns 0, but a wrong non-zero value would not be detected.

## Download matching

- [ ] **`.meta` timing is a guess.** The tracker re-reads an archive's `.meta` for about 1.75 s, assuming MO2 sometimes reports completion before writing `fileID`. Observe MO2 2.5.2's actual timing and adjust `DEFAULT_RETRY_DELAYS_MS` in `tracking/tracker.py`.
- [ ] **Duplicate-download naming is a guess.** `nexus/archives.py` ignores a trailing `(1)`-style suffix. Confirm how MO2 names a second download of the same file.
- [ ] **The download limit is read once per dialog.** Changing `max_concurrent_downloads` while the tool is open has no effect until it is reopened, because `plugin.py` passes the value to `UpdateSession` at construction. Re-reading it per download would need a narrow provider callable passed in instead of an `int`.
- [ ] **Unmatched downloads stay on "Downloading"** and keep the progress bar visible until the dialog closes. They also hold one of the `max_concurrent_downloads` slots, so queued rows can stall behind them (worst at a limit of 1). By decision there is no "Locate archive…" action, because users can update manually. A timeout that marks the row as unmatched and releases the progress count could still be worth adding (`app/session.py` plus `ui/dialog.py`).

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

## Observations outside the plugin

- Nexus's newer archive names (`<name> <modid> <version> <date> <sqid>.<ext>`) probably aren't recognised by MO2 2.5.2's own filename parsing, so manually added archives without a `.meta` may not get a mod ID. This is inferred, not tested. The bundled `nexus_mod_id_fixer` plugin already recognises the new format.
