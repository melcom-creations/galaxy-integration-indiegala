# Changelog

All notable changes to this plugin will be documented in this file.

---

## Version 2.0.2-64bit

### Overview for Version 2.0.2-64bit

This release adds local installation support and playtime tracking for IndieGala games. It also improves the external login workflow with Windows default-browser detection, support for additional browsers, and a more reliable debugging connection.

### Added in Version 2.0.2-64bit

- **Games folder setup:** The initial connection page now asks for a dedicated games folder named `IndieGala`, validates or creates it, and stores the selection outside the plugin directory.
- **Local installation detection and launching:** Owned games placed in matching subfolders below the configured `IndieGala` folder are detected as installed and can be launched directly from Galaxy.
- **Running state and local Game-Time:** The plugin monitors detected game processes, updates Galaxy's running state, and persistently records local playtime using the same process-aware approach as the ZOOM Platform integration.
- **Windows default-browser selection:** The plugin reads the registered HTTPS browser and prefers it when it is Firefox, Edge, Chrome, Opera, or Vivaldi. Unsupported default browsers fall back to an installed supported browser.
- **Additional browser support:** Firefox, Opera, and Vivaldi can now be used for the external IndieGala login in addition to Edge and Chrome. Firefox uses WebDriver BiDi, while the Chromium-based browsers use their debugging protocol.
- **Games-folder editor:** The Install action in an uninstalled IndieGala game's context menu now opens a dedicated editor with the current games folder prefilled. The path can be typed or selected, validated, saved, and applied to local game detection immediately. No game files are downloaded, installed, moved, or removed.

### Changed in Version 2.0.2-64bit

- **Temporary browser profiles:** Each external login now uses a fresh browser profile in the Windows temporary-files directory. The profile is removed after Galaxy stores the required IndieGala cookies, preventing browser caches from accumulating in the plugin data directory.
- **More compact connection page:** The initial connection window was reduced in height while retaining all folder and login controls.
- **Visible folder-change guidance:** The connection page and README now clearly explain that the games folder can later be changed by right-clicking an uninstalled IndieGala game tile and selecting Install.
- **Transparent local data documentation:** The connection page and README now state where Galaxy credentials, plugin caches, the games-folder setting, and temporary browser profiles are stored, as well as what Disconnect removes and what can remain locally.
- **Flexible games folder input:** Paths ending in either `IndieGala` or `IndieGala\` are normalized to the same folder. Capitalization does not affect validation.
- **Duplicate dialog protection:** Repeated Install requests while the games-folder editor is already open no longer create additional windows.
- **Bounded shutdown:** The local authentication server is closed before the external browser bridge, and browser monitoring, browser cleanup, and HTTP-session cleanup now have strict time limits so an abandoned login cannot leave the plugin process running after Galaxy exits.
- **Dependency refresh:** Bundled runtime packages were updated for the maintained Python 3.13 64-bit environment, and `psutil` was added for local process monitoring.

### Fixed in Version 2.0.2-64bit

- **Install action was initially unavailable:** The plugin now reports Windows compatibility directly, allowing Galaxy to enable the context-menu Install action without waiting for unavailable IndieGala GamesDB metadata.
- **Edge could start without exposing its generated debugging-port file:** The external login now reserves a private IPv4 loopback port and verifies the browser directly instead of depending on `DevToolsActivePort`, which current Edge versions may not create for an existing dedicated profile.
- **Firefox defaulted to Edge:** Firefox now uses its native WebDriver BiDi interface for session verification instead of being skipped in favor of a Chromium-based fallback browser.
- **Reconnect after changing the IndieGala account could fail:** The revised external-browser connection can reopen the dedicated login session without triggering the previous embedded-browser crash path.

### Packages Updated for Version 2.0.2-64bit

`aiohappyeyeballs` 2.7.1, `aiohttp` 3.14.3, `certifi` 2026.7.22, `chardet` 7.6.0, `idna` 3.19, `typing_extensions` 4.16.0, `yarl` 1.24.5

### Package Added for Version 2.0.2-64bit

`psutil` 7.2.2

---

## Version 2.0.1-64bit (Unreleased)

### Overview for Version 2.0.1-64bit

This release updates the original IndieGala community integration for GOG Galaxy 2.1+ and its 64-bit Python 3.13 runtime. It replaces the crash-prone embedded IndieGala login with a secure external-browser workflow, modernizes the bundled dependencies, and hardens account and cookie handling while preserving the original Showcase library import.

### Added in Version 2.0.1-64bit

- **External-browser authentication:** IndieGala sign-in now opens in a dedicated Edge or Chrome profile instead of loading the website inside Galaxy's embedded Qt web engine. Galaxy displays only a local status page while authentication is completed in the external browser.
- **Local authentication bridge:** A temporary service bound exclusively to `127.0.0.1` verifies the completed browser session and transfers only cookies belonging to `indiegala.com`. Requests are protected by loopback checks and a randomly generated nonce; the plugin never receives the account password or two-factor authentication code.
- **GitHub release update metadata:** `manifest.json` now contains the repository and GitHub Releases endpoints required by the external plugin updater once the dedicated IndieGala repository is available.

### Changed in Version 2.0.1-64bit

- **64-bit Python 3.13 dependency rebuild:** The legacy Python 3.7 32-bit packages and `cp37-win32` extension modules were replaced with current Python 3.13 64-bit dependencies and `cp313-win_amd64` extension modules.
- **Bundled dependency organization:** Third-party packages, package metadata, and utilities were moved from the plugin root into `/modules/`.
- **Resilient dependency loading:** The startup loader discovers `modules`, `Modules`, or another capitalization variant, normalizes paths with or without a trailing separator, and prevents duplicate `sys.path` entries.
- **Updated plugin metadata:** The manifest now identifies the maintained 64-bit integration, its current maintainer, GOG Galaxy 2.1+ compatibility, and the planned standalone repository.

### Fixed in Version 2.0.1-64bit

- **Galaxy crashed after successful IndieGala sign-in:** Completing authentication in the embedded Galaxy browser could terminate the client inside `Qt6WebEngineCore.dll`. IndieGala pages are no longer loaded by that component, preventing the affected login path from triggering the crash.
- **Expired stored sessions could block reconnection:** Invalid saved cookies are now cleared before the plugin starts a fresh external login.
- **Cookie scope was incomplete:** Restored browser cookies are now associated with the IndieGala HTTPS origin before account verification and later API requests.
- **Incomplete account responses caused unexpected failures:** Missing or malformed IndieGala identity data now returns through Galaxy's normal authentication-required flow instead of raising an unhandled parsing exception.
- **Authentication resources could remain active after a failed attempt:** The local server and external-browser monitoring state are stopped reliably when authentication succeeds, fails, or the plugin shuts down.

### Packages Updated for Version 2.0.1-64bit

`aiohappyeyeballs`, `aiohttp`, `aiosignal`, `async_timeout`, `attrs`, `certifi`, `chardet`, `frozenlist`, `galaxy_plugin_api`, `idna`, `multidict`, `propcache`, `typing_extensions`, `yarl`

---

## Version 0.1.0 and Earlier

*(Legacy releases by [Chris Burnham](https://github.com/burnhamup) - see the [original repository](https://github.com/burnhamup/galaxy-integration-indiegala) for historical changelog entries.)*
