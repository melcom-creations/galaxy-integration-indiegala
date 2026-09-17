# IndieGala Integration Plugin for GOG Galaxy 2.1+ (64-bit)

This plugin imports your IndieGala Showcase library into GOG Galaxy 2.1+ 64-bit. Based on the original community integration, it has been updated for the current GOG Galaxy client and Python 3.13, with an external-browser authentication workflow that avoids the embedded Galaxy web engine.

The steps below are for Windows. Dependencies are bundled; no separate Python installation is needed.

[Installation](#-installation) | [First Start](#-first-start-and-initial-sync) | [Troubleshooting](#-troubleshooting) | [Support & Feedback](#-support--feedback)

## ✨ Features

* Imports games listed in your IndieGala Showcase collection into GOG Galaxy
* Uses a temporary external Firefox, Edge, Chrome, Opera, or Vivaldi profile for IndieGala sign-in
* Keeps the IndieGala website out of Galaxy's embedded browser
* Stores IndieGala browser cookies locally through Galaxy's integration credential storage
* Detects and launches games installed below a configured `IndieGala` folder
* Tracks local playtime while detected IndieGala games are running
* Opens the games-folder editor through the Install action in an uninstalled IndieGala game's context menu
* Includes bundled Python 3.13 64-bit dependencies in `/modules/`

> [!NOTE]
> IndieGala determines which games appear in the Showcase collection. Purchases, bundles, or other titles that IndieGala does not list there cannot be imported by this plugin.

## 📦 Installation

### 🔄 Automatic Installation with Plugin Updater

Use the [melcom GOG Galaxy Plugin Updater](https://github.com/melcom-creations/galaxy-integrations-64bit/tree/main/tools/melcom-galaxy_plugin_updater) to install or update the integration when IndieGala is listed in your updater version. Otherwise, use the manual steps below.

1. Download and extract the Plugin Updater.
2. Double-click `update-plugins.bat`.
3. Select your preferred language and follow the displayed instructions.

### 📂 Manual Installation

1. Close GOG Galaxy completely, including the system tray application.
2. Download the [latest release package](https://github.com/melcom-creations/galaxy-integration-indiegala/releases/latest).
3. Extract the plugin folder from the ZIP archive into:

   ```text
   %localappdata%\GOG.com\Galaxy\plugins\installed\
   ```

Place `manifest.json` directly inside this folder, without an extra nested plugin folder:

```text
%localappdata%\GOG.com\Galaxy\plugins\installed\indiegala_a1a85742-f3e0-42ae-bde9-64ab7d0321cf\
```

> [!IMPORTANT]
> Do not place backup copies of this plugin inside the `plugins\installed` directory. GOG Galaxy scans every folder inside this directory during startup, so duplicate plugin folders can cause GUID conflicts or load an outdated version.

**Next step:** Continue with [First Start and Initial Sync](#-first-start-and-initial-sync).

## 🚀 First Start and Initial Sync

For the first synchronization after installing or updating the plugin:

1. Start GOG Galaxy.
2. Open **Settings -> Integrations -> IndieGala** and click **Connect**.
3. Galaxy displays a local connection page. Enter the full path where your IndieGala games are or will be installed.
4. Use a dedicated folder named `IndieGala`, for example `C:\Games\IndieGala`. The plugin creates the folder if necessary. The final folder name must be `IndieGala`, regardless of capitalization.
5. Click **Open IndieGala Login**.
6. Complete the sign-in in the temporary browser window. Enter your password and any two-factor authentication code only there.
7. Wait until the local Galaxy page confirms that the IndieGala login was verified.
8. Open the account menu in the top-right corner of GOG Galaxy and select **Sync integrations** once.
9. Wait until the synchronization has finished. Metadata and cover art can take a few minutes to appear in Galaxy.

The plugin uses Firefox, Edge, Chrome, Opera, or Vivaldi. It prefers your Windows default browser if supported; otherwise, it uses the first installed supported browser.

## 📂 Game Installation and Folders

Place each installed game in its own direct subfolder below the configured `IndieGala` folder. The subfolder name must match either the game title shown by IndieGala or its IndieGala slug. Matching ignores capitalization, spaces, and punctuation. If the plugin finds a safe game executable there, Galaxy marks the game as installed, enables launching, detects its running state, and records local playtime.

### 🔧 Changing the Games Folder Later

> [!TIP]
> Right-click any uninstalled IndieGala game tile in Galaxy and select **Install**. The gray main **Install** button is not used for this feature.

The games-folder editor displays the current path, which can be changed directly or selected with **Browse**. The final folder must still be named `IndieGala`; a trailing backslash is optional. Saving the new path immediately refreshes local game detection.

IndieGala games must still be downloaded and installed manually. The plugin does not download, install, move, or remove game files.

## 🔒 Authentication and Local Data

The plugin reads only cookies belonging to `indiegala.com` from the temporary browser profile. It never receives or stores your email address, password, or two-factor authentication code. After Galaxy stores the cookies as integration credentials, the browser is closed and the temporary profile is removed. A new browser login is required only when the stored IndieGala session expires, is revoked, is reset, or the integration is disconnected.

Firefox uses WebDriver BiDi; Chromium-based browsers use their debugging protocol.

### 💾 Data Storage and What Disconnect Removes

| Data | Local location | Result of clicking **Disconnect** |
|---|---|---|
| IndieGala browser cookies used for authentication | Galaxy's credential area inside `%ProgramData%\GOG.com\Galaxy\storage\plugins\indiegala_a1a85742-f3e0-42ae-bde9-64ab7d0321cf-<Galaxy-user-id>-storage.db` | Removed by Galaxy |
| Cached IndieGala library and locally tracked Game-Time | The normal cache area inside the same Galaxy plugin database | The local cache can remain |
| Configured IndieGala games folder | `%LOCALAPPDATA%\melcom-creations\GOG Galaxy Integrations\IndieGala\settings.json` | Remains |
| Temporary login browser profile | `%TEMP%\melcom-indiegala-auth-<browser>-<random>\` | Removed after normal login completion or plugin shutdown |

Clicking **Disconnect** removes the stored IndieGala authentication cookies. It does not delete the plugin database itself, the cached library and local Game-Time data inside it, or `settings.json`. If Galaxy, the plugin, or Windows is terminated before cleanup finishes, a clearly named temporary browser folder can remain under `%TEMP%`; it contains no password or two-factor authentication code and can be removed after all related browser and Galaxy processes are closed.

For complete local removal, first disconnect the integration and close Galaxy completely. Then remove only the IndieGala database named above, the IndieGala settings directory, and any remaining `%TEMP%\melcom-indiegala-auth-*` directories. Do not remove database or temporary files belonging to other applications.

## 🛠️ Troubleshooting

Restart Galaxy and try one synchronization. If the problem remains, collect a fresh log. A database reset is not required for this.

### 🧪 Create a Fresh Diagnostic Log

1. Close GOG Galaxy completely, including the system tray application.
2. Open `%ProgramData%\GOG.com\Galaxy\logs\`. Move the existing `plugin-indiegala-a1a85742-f3e0-42ae-bde9-64ab7d0321cf.log` to a backup folder outside this directory, if present. Leave other logs in place.
3. Start Galaxy, reproduce the problem once, then close Galaxy completely to finish writing the log.
4. Send the newly created plugin log, not the entire folder. Include the plugin and Galaxy versions, your steps, the expected and actual result, and whether the problem can be reproduced.

See [Support & Feedback](#-support--feedback) for contact options.

### 🔄 Reset Plugin Storage (Last Resort)

Use this only if restarting and synchronizing do not help, or when requested for troubleshooting. Cached library data and local playtime may be lost; signing in again may be required. Keep the backup. The separate games-folder setting remains unchanged.

1. Close GOG Galaxy completely, including the system tray application.
2. Open `%ProgramData%\GOG.com\Galaxy\storage\plugins\`.
3. Find the active `indiegala_...-storage.db` file for your Galaxy account. If unsure which file is correct, stop. Leave other integrations' databases unchanged.
4. Append `.old` to its name. If that backup already exists, use an unused suffix; never overwrite it.
5. Start Galaxy, reconnect if necessary, and select **Sync integrations** once. Wait until it finishes.

To undo: close Galaxy, rename the new database to an unused backup name, then restore the saved database's original name. Never restore it while Galaxy is running.

## 🙏 Credits

**Original Community Integration**  
Chris Burnham and contributors  
[burnhamup/galaxy-integration-indiegala](https://github.com/burnhamup/galaxy-integration-indiegala)

**64-bit Port, Python 3.13 Compatibility, External Browser Authentication and Maintenance**  
melcom

## 🤝 Support & Feedback

**GitHub Issues are intentionally disabled.** Health-related limitations prevent me from reliably managing separate issue trackers across all of my plugin repositories.

Before contacting me, follow [Troubleshooting](#-troubleshooting) and prepare a fresh IndieGala plugin log with a detailed description.

* **GOG:** Send me a message or add me as a friend through my [GOG profile](https://www.gog.com/u/melcom).
* **Email:** `melcom @ gmx.net`
* **Discord:** `.melcom` - the leading dot is part of the username. You can send me a message or add me as a friend.

Logs can be attached directly or shared using an accessible cloud storage link, such as Dropbox, OneDrive, Google Drive, or a similar service. Response times may vary depending on my health and available development time. Thank you for your understanding.
