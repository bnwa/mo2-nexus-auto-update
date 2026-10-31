# Nexus Auto Updater

A Mod Organizer 2 tool plugin that finds the right replacement file for your outdated Nexus mods, downloads it through MO2's own download manager, and hands it to MO2's installer.

MO2 can already tell you *that* a mod has an update. This plugin works out *which file* to download: it looks at the exact file you have installed, finds the newest file the author published as its successor (even if they renamed it), and queues that download for you — for one mod or hundreds at once.

> **Status: pre-alpha (0.1.0).** It relies on the Nexus Mods v2 API, which Nexus itself labels as beta. Expect rough edges; see [`TODO.md`](TODO.md).

## Requirements

- Mod Organizer 2 **2.5.2** or newer with Python plugin support (the standard install includes it).
- You are **logged in to Nexus Mods in MO2** (Settings → Nexus).
- A **Nexus Mods Premium** account. Downloads are started directly through MO2's download manager, which needs Premium to download without visiting the website.
- An internet connection.

No API key needs to be entered in the plugin. It reads public file information from the Nexus v2 API without logging in, and all downloads use MO2's existing Nexus login.

## Installation

1. Close Mod Organizer 2.
2. Copy the whole `nexus_auto_updater` folder into MO2's `plugins` folder, so you have:
   ```
   <Mod Organizer 2>/plugins/nexus_auto_updater/__init__.py
   <Mod Organizer 2>/plugins/nexus_auto_updater/plugin.py
   <Mod Organizer 2>/plugins/nexus_auto_updater/app/ ...
   ```
3. Start MO2. The plugin appears under **Tools → Nexus Auto Updater**.

## Usage

1. **Let MO2 find outdated mods.** Run MO2's own *Check for updates* (the Nexus update check in the mod list). The plugin starts from the mods MO2 flags as having an update.
2. **Open Tools → Nexus Auto Updater.** It checks each flagged mod against Nexus and fills a table as it goes. A progress bar shows how far along it is.
3. **Review the table.**
   - Rows marked **Ready** are ticked automatically.
   - Rows marked **Needs review** are not ticked: check the *Update to* column and tick them yourself if the suggestion looks right.
   - Use the checkbox in the column header to tick or untick every downloadable row at once.
   - *Hide up-to-date* (on by default) hides rows that don't need anything.
4. **Click _Download checked_.** The downloads appear in MO2's Downloads tab, and the progress bar tracks them. Rows switch to **Downloaded** as they finish.
5. **Click _Install downloaded…_.** MO2 installs each update in turn:
   - When it asks what to do with the existing mod, choose **Replace** to keep the mod's position and enabled state.
   - Mods with a FOMOD installer show their installer as usual.

Hover over a cell for more detail: file IDs, MO2's recorded versions, and the full reason for each decision.

### What the statuses mean

| Status | Meaning |
|---|---|
| **Ready** | A clear successor of your installed file was found. Ticked by default. |
| **Needs review** | A likely successor was found, but the plugin isn't certain (for example the author reorganised their files, or Nexus returned incomplete data). Not ticked by default. |
| **Up to date** | Your installed file is still the newest in its line. Hidden by default. |
| **Unresolved** | The plugin couldn't determine a successor. Update this mod manually. |
| **Skipped** | The plugin can't download for this mod (for example it belongs to a different game than the one MO2 is managing). |
| **Error** | Nexus couldn't be asked about this mod (network trouble, hidden mod, etc.). |

After an action starts, the status shows its progress instead: **Downloading**, **Downloaded**, **Download failed**, **Installed**, **Install cancelled**.

A mod installed from several Nexus files (for example a main file plus an optional add-on) gets one row per file.

## Settings

In MO2, open **Settings → Plugins → Nexus Auto Updater**:

| Setting | Default | Effect |
|---|---|---|
| `scan_all_nexus_mods` | off | Check every Nexus mod, not only the ones MO2 flags as outdated. Useful when MO2's update check is stale, but slower. |
| `request_delay_ms` | 250 | Minimum delay between requests to Nexus, in milliseconds. Raise it if Nexus rate-limits you. |

## Troubleshooting

- **"No mods to check."** MO2 hasn't flagged any mod as outdated. Run MO2's update check first, or enable `scan_all_nexus_mods`.
- **"Scan stopped: the Nexus v2 API (beta) no longer answers the way this plugin expects."** Nexus changed its beta API. The plugin stops instead of guessing; it likely needs an update. You can still update mods manually in MO2.
- **"Scan stopped: Nexus is not responding."** Several requests in a row failed. Try again later.
- **A row stays on _Downloading_ after MO2 finished the download.** The plugin couldn't match the finished archive to the row. The file is still in MO2's Downloads tab: install it from there as you normally would. Closing and reopening the tool clears the row.
- **A mod is _Skipped_ as belonging to another game.** MO2's plugin interface can only start downloads for the game the instance manages. Update that mod manually.

