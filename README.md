# IndieGala Integration Plugin for GOG Galaxy 2.1+ (64-bit)

This plugin imports your IndieGala Showcase library into GOG Galaxy 2.1+ 64-bit. It provides external-browser authentication, local game detection, launching, and Game-Time tracking for the current Galaxy client and Python 3.13.

---

## ✨ Features

* Imports games from your IndieGala Showcase collection
* Signs in through Firefox, Edge, Chrome, Opera, or Vivaldi instead of Galaxy's embedded browser
* Detects and launches games below a configured `IndieGala` folder
* Tracks local Game-Time while a detected game is running
* Lets you change the games folder directly from Galaxy
* Includes the required Python 3.13 64-bit dependencies

> [!NOTE]
> Only games listed in your IndieGala Showcase collection can be imported.

---

## 📦 Installation

### Plugin Updater (Recommended)

Once IndieGala is available in the [melcom GOG Galaxy Plugin Updater](https://github.com/melcom-creations/galaxy-integrations-64bit/tree/main/tools/melcom-galaxy_plugin_updater), run `update-plugins.bat` and follow its instructions.

### Manual Installation

1. Close GOG Galaxy completely, including its system tray application.
2. Download the latest release package and extract it into:

   ```text
   %localappdata%\GOG.com\Galaxy\plugins\installed\
   ```

3. Confirm that the archive created this folder:

   ```text
   indiegala_a1a85742-f3e0-42ae-bde9-64ab7d0321cf
   ```

> [!IMPORTANT]
> Do not keep backup copies inside `plugins\installed`. Duplicate plugin folders can cause GUID conflicts or load an outdated version.

---

## 🚀 First Connection

1. Start GOG Galaxy and open **Settings -> Integrations -> IndieGala -> Connect**.
2. Enter a dedicated games folder ending in `IndieGala`, for example `C:\Games\IndieGala` or `E:\Games\IndieGala\`. Capitalization and a trailing backslash do not matter. The folder is created if necessary.
3. Click **Open IndieGala Login** and sign in through the separate browser window.
4. Wait for confirmation, then select **Sync integrations** once from Galaxy's account menu.

![IndieGala connection page with games-folder selection and external-browser login](images/01-screenshot_2026-09-03_060121.png)

The plugin prefers your Windows default browser when it is supported. It reads only `indiegala.com` cookies from a temporary browser profile and never receives your password or two-factor authentication code. The profile is removed after login.

---

## 📁 Changing the Games Folder

Right-click any uninstalled IndieGala game tile and select **Install**. The gray main Install button is not used for this feature.

![Install action in an uninstalled IndieGala game's context menu](images/02-screenshot_2026-09-03_055839.jpg)

Enter the new path or use **Browse**, then click **Save**. The final folder must still be named `IndieGala`; a trailing backslash is optional.

![IndieGala games-folder editor with the current path](images/03-screenshot_2026-09-03_055904.jpg)

This action changes only the configured path. It does not download, install, move, or remove games.

---

## 🎮 Installed Games and Game-Time

Download and install IndieGala games manually. Each game must use its own direct subfolder below the configured `IndieGala` folder. The subfolder name must match the IndieGala title or slug; capitalization, spaces, and punctuation are ignored.

```text
E:\Games\IndieGala\8BitBoy\
```

Once a suitable executable is detected, Galaxy marks the game as installed, enables the purple **Play** button, detects its running state, and records local Game-Time.

![Detected IndieGala installation with the Play button and locally tracked Game-Time](images/04-screenshot_2026-09-03_055730.jpg)

---

## 🔐 Local Data and Disconnect

| Data | Location | Removed by **Disconnect** |
|---|---|---|
| Authentication cookies | `%ProgramData%\GOG.com\Galaxy\storage\plugins\indiegala_a1a85742-f3e0-42ae-bde9-64ab7d0321cf-<Galaxy-user-id>-storage.db` | Yes |
| Library cache and Game-Time | Same Galaxy plugin database | Not necessarily |
| Games-folder setting | `%LOCALAPPDATA%\melcom-creations\GOG Galaxy Integrations\IndieGala\settings.json` | No |
| Temporary browser profile | `%TEMP%\melcom-indiegala-auth-<browser>-<random>\` | Removed after login or shutdown |

If Galaxy or Windows is terminated before cleanup finishes, a temporary `melcom-indiegala-auth-*` folder can remain under `%TEMP%`. Close Galaxy and the related browser before removing it manually.

---

## 🛠️ Troubleshooting

If restarting Galaxy does not help:

1. Close Galaxy completely.
2. Open `C:\ProgramData\GOG.com\Galaxy\storage\plugins\`.
3. Rename the IndieGala database ending in `-storage.db` by adding `.old`.
4. Start Galaxy, reconnect IndieGala if necessary, and select **Sync integrations** once.

If the problem remains, close Galaxy, delete the old IndieGala plugin log, start Galaxy, reproduce the issue, and close Galaxy again. Then send only the newly created file:

```text
%ProgramData%\GOG.com\Galaxy\logs\plugin-indiegala-a1a85742-f3e0-42ae-bde9-64ab7d0321cf.log
```

Include the steps taken, the expected result, and the actual result.

---

## 🙏 Credits

**Original Community Integration:** Chris Burnham and contributors  
[burnhamup/galaxy-integration-indiegala](https://github.com/burnhamup/galaxy-integration-indiegala)

**64-bit Port, Python 3.13 Compatibility, External Browser Authentication and Maintenance:** melcom

---

## 🤝 Support & Feedback

**GitHub Issues are intentionally disabled.** Health-related limitations prevent me from reliably managing separate issue trackers across all plugin repositories.

Before contacting me, prepare a fresh IndieGala plugin log and a clear description of the problem.

* **GOG:** [GOG profile](https://www.gog.com/u/melcom)
* **Email:** `melcom @ gmx.net`
* **Discord:** `.melcom` - the leading dot is part of the username

Response times may vary depending on my health and available development time. Thank you for your understanding.
