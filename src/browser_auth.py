import asyncio
import base64
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import winreg
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web
from yarl import URL

from local_settings import InstallRootError, normalize_install_root


logger = logging.getLogger(__name__)

LOGIN_URL = "https://www.indiegala.com/login"
USER_INFO_URL = "https://www.indiegala.com/login_new/user_info"
COOKIE_URLS = [
    "https://www.indiegala.com/",
    "https://www.indiegala.com/library",
    USER_INFO_URL,
]

FORM_PATH = "/"
OPEN_PATH = "/open"
STATUS_PATH = "/status"
COMPLETE_PATH = "/complete"
TEMP_PROFILE_PREFIX = "melcom-indiegala-auth-"
TEMP_PROFILE_MARKER = ".indiegala-temporary-profile"


_FORM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect IndieGala</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: "Segoe UI", system-ui, sans-serif; background: #151515;
         color: #f5f5f5; margin: 0; padding: 22px; line-height: 1.4;
         font-size: 13px; }
  h2 { margin: 0 0 10px; font-size: 19px; }
  p { margin: 8px 0; }
  .hint { color: #aaa; font-size: 12px; }
  .later-change { margin: 9px 0 12px; padding: 7px 9px; background: #241c30;
                  border-left: 3px solid #8c5bd3; color: #eee; font-size: 12px; }
  label { display: block; margin-top: 14px; font-weight: 600; }
  input { width: 100%; margin-top: 6px; padding: 9px; background: #202020;
          color: #f5f5f5; border: 1px solid #555; border-radius: 4px;
          font-family: Consolas, monospace; font-size: 12px; }
  button { margin-top: 10px; padding: 9px 18px; background: #6b45b8;
           color: #fff; border: 0; border-radius: 4px; cursor: pointer;
           font-size: 13px; }
  button:hover { background: #7c55ca; }
  button:disabled { background: #555; cursor: default; }
  #status { margin-top: 16px; padding: 10px; border: 1px solid #444;
            background: #202020; border-radius: 4px; min-height: 40px; }
  .error { color: #ff9a9a; }
</style>
</head>
<body>
  <h2>Connect IndieGala</h2>
  <p>Choose where IndieGala games will be installed.</p>
  <label for="install-root">IndieGala games folder</label>
  <input id="install-root" type="text" spellcheck="false" placeholder="C:\\Games\\IndieGala">
  <p class="hint">Recommended: create a dedicated folder such as C:\\Games\\IndieGala. The final folder must be named IndieGala. It will be created if necessary.</p>
  <p class="later-change"><strong>Change it later:</strong> Right-click an uninstalled IndieGala game tile in Galaxy and select <strong>Install</strong>.</p>
  <p>Sign in through a separate supported browser window. Galaxy will not load the IndieGala website.</p>
  <p class="hint">The plugin uses a temporary browser profile and reads only cookies belonging to indiegala.com. It never receives your password or 2FA code. Galaxy stores the cookies as integration credentials and removes them when the integration is disconnected. The temporary profile is removed after login.</p>
  <button id="open" type="button">Open IndieGala Login</button>
  <div id="status">Ready to open the secure login window.</div>
<script>
const nonce = __NONCE__;
const initialInstallRoot = __INSTALL_ROOT__;
const openButton = document.getElementById("open");
const statusBox = document.getElementById("status");
const installRootInput = document.getElementById("install-root");
installRootInput.value = initialInstallRoot;

openButton.addEventListener("click", function () {
  const installRoot = installRootInput.value.trim();
  if (!installRoot) {
    statusBox.className = "error";
    statusBox.textContent = "Enter the full path to your IndieGala folder.";
    installRootInput.focus();
    return;
  }
  openButton.disabled = true;
  installRootInput.disabled = true;
  statusBox.className = "";
  statusBox.textContent = "Opening a supported browser. Complete the IndieGala login there.";
  fetch("/open?nonce=" + encodeURIComponent(nonce), {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ install_root: installRoot })
  })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error(data.message || "Could not start the browser.");
        }
        statusBox.textContent = "Waiting for the IndieGala login in " + (data.browser || "the browser") + "...";
        return data;
      });
    })
    .catch(function (error) {
      statusBox.className = "error";
      statusBox.textContent = error.message;
      openButton.disabled = false;
      installRootInput.disabled = false;
    });
});

function pollStatus() {
  fetch("/status?nonce=" + encodeURIComponent(nonce), { cache: "no-store" })
    .then(function (response) { return response.json(); })
    .then(function (data) {
      if (data.state === "ready") {
        statusBox.className = "";
        statusBox.textContent = "IndieGala login verified. Finishing connection.";
        window.location.href = "/complete";
        return;
      }
      if (data.state === "error") {
        statusBox.className = "error";
        statusBox.textContent = data.message || "The external login could not be verified.";
        openButton.disabled = false;
        installRootInput.disabled = false;
      } else if (data.state === "waiting") {
        statusBox.className = "";
        statusBox.textContent = "Waiting for the IndieGala login in " + (data.browser || "the browser") + "...";
      }
    })
    .catch(function () {});
}

setInterval(pollStatus, 1000);
pollStatus();
</script>
</body>
</html>
"""


_COMPLETE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>IndieGala connected</title>
<style>body{font-family:"Segoe UI",system-ui,sans-serif;background:#151515;color:#f5f5f5;
margin:0;padding:30px;}</style></head>
<body><h2>IndieGala login verified</h2><p>You can close this window.</p></body></html>
"""


@dataclass
class BrowserAuthResult:
    cookies: dict[str, str]
    user_id: str
    user_name: str


def _create_temporary_browser_profile(browser_kind: str) -> Path:
    temp_root = Path(tempfile.gettempdir()).resolve()
    profile_path = Path(
        tempfile.mkdtemp(
            prefix=f"{TEMP_PROFILE_PREFIX}{browser_kind}-",
            dir=temp_root,
        )
    ).resolve()
    (profile_path / TEMP_PROFILE_MARKER).write_text(
        "Temporary IndieGala browser authentication profile.\n",
        encoding="utf-8",
    )
    return profile_path


def _remove_temporary_browser_profile(profile_path: Path) -> None:
    resolved_path = profile_path.resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    marker = resolved_path / TEMP_PROFILE_MARKER
    if (
        resolved_path.parent != temp_root
        or not resolved_path.name.startswith(TEMP_PROFILE_PREFIX)
        or not marker.is_file()
    ):
        raise OSError(f"Refusing to remove unverified browser profile: {resolved_path}")

    for attempt in range(3):
        try:
            shutil.rmtree(resolved_path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(0.25)


@dataclass(frozen=True)
class BrowserInfo:
    executable: Path
    kind: str
    display_name: str


def _browser_kind(executable: Path) -> Optional[str]:
    name = executable.name.casefold()
    path_text = str(executable).casefold()
    if name == "msedge.exe":
        return "edge"
    if name == "chrome.exe":
        return "chrome"
    if name == "firefox.exe":
        return "firefox"
    if name == "vivaldi.exe":
        return "vivaldi"
    if name == "opera.exe" or (name == "launcher.exe" and "opera" in path_text):
        return "opera"
    return None


def _browser_display_name(kind: str) -> str:
    return {
        "edge": "Microsoft Edge",
        "chrome": "Google Chrome",
        "firefox": "Mozilla Firefox",
        "opera": "Opera",
        "vivaldi": "Vivaldi",
    }[kind]


def _executable_from_command(command: object) -> Optional[Path]:
    if not isinstance(command, str) or not command.strip():
        return None
    match = re.match(r'^\s*"([^"]+\.exe)"|^\s*([^\s]+\.exe)', command, re.IGNORECASE)
    if match is None:
        return None
    return Path(os.path.expandvars(match.group(1) or match.group(2)))


def _default_browser_executable() -> tuple[Optional[Path], Optional[str]]:
    user_choice = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, user_choice) as key:
            prog_id = str(winreg.QueryValueEx(key, "ProgId")[0])
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT,
            rf"{prog_id}\shell\open\command",
        ) as key:
            command = winreg.QueryValueEx(key, "")[0]
    except OSError:
        return None, None
    return _executable_from_command(command), prog_id


def _registered_browser_executables() -> list[Path]:
    registry_path = r"SOFTWARE\Clients\StartMenuInternet"
    executables: list[Path] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            root = winreg.OpenKey(hive, registry_path)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    browser_key = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(
                        root,
                        rf"{browser_key}\shell\open\command",
                    ) as command_key:
                        executable = _executable_from_command(
                            winreg.QueryValueEx(command_key, "")[0]
                        )
                except OSError:
                    continue
                if executable is not None:
                    executables.append(executable)
    return executables


def _find_browser() -> BrowserInfo:
    candidates: list[Path] = []
    default_executable, default_prog_id = _default_browser_executable()
    if default_executable is not None:
        default_kind = _browser_kind(default_executable)
        if default_kind is not None and default_executable.is_file():
            logger.info(
                "Using supported Windows default browser for IndieGala login: %s",
                _browser_display_name(default_kind),
            )
            return BrowserInfo(
                default_executable,
                default_kind,
                _browser_display_name(default_kind),
            )
        logger.info(
            "Windows default browser is not compatible with IndieGala session transfer: %s",
            default_prog_id or default_executable.name,
        )

    candidates.extend(_registered_browser_executables())
    for environment_name, relative_paths in (
        (
            "ProgramFiles(x86)",
            [
                Path("Microsoft/Edge/Application/msedge.exe"),
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Mozilla Firefox/firefox.exe"),
                Path("Vivaldi/Application/vivaldi.exe"),
                Path("Opera/launcher.exe"),
            ],
        ),
        (
            "ProgramFiles",
            [
                Path("Microsoft/Edge/Application/msedge.exe"),
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Mozilla Firefox/firefox.exe"),
                Path("Vivaldi/Application/vivaldi.exe"),
                Path("Opera/launcher.exe"),
            ],
        ),
        (
            "LOCALAPPDATA",
            [
                Path("Microsoft/Edge/Application/msedge.exe"),
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Mozilla Firefox/firefox.exe"),
                Path("Vivaldi/Application/vivaldi.exe"),
                Path("Programs/Opera/launcher.exe"),
                Path("Programs/Opera GX/launcher.exe"),
            ],
        ),
    ):
        base = os.environ.get(environment_name)
        if base:
            candidates.extend(Path(base) / relative_path for relative_path in relative_paths)

    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(str(candidate)))
        if normalized in seen:
            continue
        seen.add(normalized)
        kind = _browser_kind(candidate)
        if kind is not None and candidate.is_file():
            display_name = _browser_display_name(kind)
            logger.info("Using fallback browser for IndieGala login: %s", display_name)
            return BrowserInfo(candidate, kind, display_name)
    raise FileNotFoundError(
        "No supported browser was found. Install Firefox, Edge, Chrome, Opera, or Vivaldi."
    )


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class ExternalBrowserBridge:
    def __init__(self, profile_path: Optional[Path] = None):
        self._profile_path_override = profile_path
        self.profile_path = profile_path
        self._temporary_profile_path: Optional[Path] = None
        self.browser_name: Optional[str] = None
        self.browser_kind: Optional[str] = None
        self.state = "idle"
        self.error_message: Optional[str] = None
        self.result: Optional[BrowserAuthResult] = None
        self._process: Optional[subprocess.Popen] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._debug_port: Optional[int] = None
        self._stop_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.state == "waiting" and self._monitor_task is not None:
            return

        await self.stop(close_browser=True)
        browser = _find_browser()
        if self._profile_path_override is None:
            self.profile_path = _create_temporary_browser_profile(browser.kind)
            self._temporary_profile_path = self.profile_path
        else:
            self.profile_path = self._profile_path_override
            self.profile_path.mkdir(parents=True, exist_ok=True)
            self._temporary_profile_path = None
        profile_path = self.profile_path
        self.browser_name = browser.display_name
        self.browser_kind = browser.kind
        debug_port = _reserve_loopback_port()
        if browser.kind == "firefox":
            arguments = [
                str(browser.executable),
                "--remote-debugging-port",
                str(debug_port),
                "--remote-allow-hosts",
                "127.0.0.1",
                "-no-remote",
                "-profile",
                str(profile_path),
                "-new-window",
                LOGIN_URL,
            ]
        else:
            arguments = [
                str(browser.executable),
                "--remote-debugging-address=127.0.0.1",
                f"--remote-debugging-port={debug_port}",
                f"--user-data-dir={profile_path}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-mode",
                "--disable-features=msEdgeFirstRunExperience",
                "--new-window",
                LOGIN_URL,
            ]
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self._process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError:
            await self._cleanup_temporary_profile()
            raise
        self.state = "waiting"
        self.error_message = None
        self.result = None
        self._debug_port = debug_port
        self._monitor_task = asyncio.create_task(self._monitor(debug_port))
        logger.info(
            "Opened external IndieGala login in %s with a temporary browser profile on port %d",
            browser.display_name,
            debug_port,
        )

    async def stop(self, *, close_browser: bool) -> None:
        async with self._stop_lock:
            task = self._monitor_task
            self._monitor_task = None
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                done, pending = await asyncio.wait({task}, timeout=2)
                if task in done:
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("IndieGala browser monitor failed while stopping")
                elif pending:
                    logger.warning("IndieGala browser monitor did not stop within 2 seconds")

            process = self._process
            try:
                if close_browser:
                    await asyncio.wait_for(self._close_browser(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("Timed out while closing the dedicated IndieGala browser")
            finally:
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        try:
                            process.kill()
                        except OSError:
                            pass
                    except OSError:
                        pass
                self._process = None
                self._debug_port = None
                await self._cleanup_temporary_profile()

    async def _cleanup_temporary_profile(self) -> None:
        temporary_profile_path = self._temporary_profile_path
        if temporary_profile_path is None:
            return
        try:
            await asyncio.to_thread(
                _remove_temporary_browser_profile,
                temporary_profile_path,
            )
        except OSError:
            logger.exception(
                "Unable to remove temporary IndieGala browser profile: %s",
                temporary_profile_path,
            )
            return

        self._temporary_profile_path = None
        if self.profile_path == temporary_profile_path:
            self.profile_path = None
        logger.info(
            "Removed temporary IndieGala browser profile: %s",
            temporary_profile_path,
        )

    async def _monitor(self, debug_port: int) -> None:
        try:
            await self._wait_for_debug_connection(debug_port)
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if self.browser_kind == "firefox":
                    await self._monitor_firefox(session, debug_port)
                    if self.result is not None:
                        return
                else:
                    for _ in range(900):
                        result = await self._try_capture(session, debug_port)
                        if result is not None:
                            self.result = result
                            self.state = "ready"
                            logger.info("External IndieGala browser session validated successfully")
                            return
                        await asyncio.sleep(1)
            raise TimeoutError("The IndieGala login timed out")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("External IndieGala browser login failed: %s", error)
            self.error_message = str(error)
            self.state = "error"

    async def _wait_for_debug_connection(self, debug_port: int) -> None:
        if self.browser_kind == "firefox":
            endpoint = f"http://127.0.0.1:{debug_port}/"
        else:
            endpoint = f"http://127.0.0.1:{debug_port}/json/version"
        timeout = aiohttp.ClientTimeout(total=1)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _ in range(150):
                try:
                    async with session.get(endpoint) as response:
                        if response.status == 200:
                            if self.browser_kind == "firefox":
                                return
                            payload = await response.json(content_type=None)
                            websocket_url = payload.get("webSocketDebuggerUrl")
                            if isinstance(websocket_url, str) and websocket_url:
                                return
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                    pass
                await asyncio.sleep(0.2)
        raise TimeoutError("The browser debugging connection did not become available")

    async def _monitor_firefox(
        self,
        session: aiohttp.ClientSession,
        debug_port: int,
    ) -> None:
        websocket_url = f"ws://127.0.0.1:{debug_port}/session"
        async with session.ws_connect(
            websocket_url,
            protocols=("webdriver-bidi",),
            max_msg_size=2 * 1024 * 1024,
        ) as socket:
            await self._send_bidi_command(
                socket,
                1,
                "session.new",
                {"capabilities": {}},
            )
            for command_id in range(2, 902):
                payload = await self._send_bidi_command(
                    socket,
                    command_id,
                    "storage.getCookies",
                    {},
                )
                browser_cookies = self._convert_bidi_cookies(payload.get("cookies", []))
                result = await self._validate_cookies(session, browser_cookies)
                if result is not None:
                    self.result = result
                    self.state = "ready"
                    logger.info("External IndieGala Firefox session validated successfully")
                    return
                await asyncio.sleep(1)

    @staticmethod
    async def _send_bidi_command(
        socket: aiohttp.ClientWebSocketResponse,
        command_id: int,
        method: str,
        params: dict,
    ) -> dict:
        await socket.send_json(
            {
                "id": command_id,
                "method": method,
                "params": params,
            }
        )
        while True:
            message = await socket.receive(timeout=8)
            if message.type != aiohttp.WSMsgType.TEXT:
                raise ConnectionError("The Firefox WebDriver BiDi connection was closed")
            payload = json.loads(message.data)
            if payload.get("id") != command_id:
                continue
            if payload.get("type") == "error":
                raise RuntimeError(
                    str(payload.get("message") or payload.get("error") or "Firefox WebDriver BiDi command failed")
                )
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Firefox WebDriver BiDi returned an invalid response")
            return result

    @staticmethod
    def _convert_bidi_cookies(cookies: object) -> list[dict]:
        if not isinstance(cookies, list):
            return []
        converted = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            value_data = cookie.get("value")
            if not isinstance(value_data, dict):
                continue
            value_type = value_data.get("type")
            value = value_data.get("value")
            if not isinstance(value, str):
                continue
            if value_type == "base64":
                try:
                    value = base64.b64decode(value, validate=True).decode("latin-1")
                except (ValueError, UnicodeError):
                    continue
            elif value_type != "string":
                continue
            converted.append(
                {
                    "name": cookie.get("name"),
                    "value": value,
                    "domain": cookie.get("domain"),
                    "path": cookie.get("path"),
                    "secure": cookie.get("secure", False),
                }
            )
        return converted

    async def _try_capture(
        self,
        session: aiohttp.ClientSession,
        debug_port: int,
    ) -> Optional[BrowserAuthResult]:
        targets_url = f"http://127.0.0.1:{debug_port}/json/list"
        try:
            async with session.get(targets_url) as response:
                if response.status != 200:
                    return None
                targets = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return None

        for target in targets:
            if target.get("type") != "page":
                continue
            page_url = str(target.get("url", ""))
            if not page_url.startswith("https://www.indiegala.com"):
                continue
            websocket_url = target.get("webSocketDebuggerUrl")
            if not isinstance(websocket_url, str):
                continue
            cookies = await self._read_cookies(session, websocket_url)
            result = await self._validate_cookies(session, cookies)
            if result is not None:
                return result
        return None

    async def _read_cookies(
        self,
        session: aiohttp.ClientSession,
        websocket_url: str,
    ) -> list[dict]:
        try:
            async with session.ws_connect(websocket_url, max_msg_size=2 * 1024 * 1024) as socket:
                await socket.send_json(
                    {
                        "id": 1,
                        "method": "Network.getCookies",
                        "params": {"urls": COOKIE_URLS},
                    }
                )
                for _ in range(50):
                    message = await socket.receive(timeout=5)
                    if message.type != aiohttp.WSMsgType.TEXT:
                        return []
                    payload = json.loads(message.data)
                    if payload.get("id") == 1:
                        return payload.get("result", {}).get("cookies", [])
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return []
        return []

    async def _validate_cookies(
        self,
        session: aiohttp.ClientSession,
        browser_cookies: list[dict],
    ) -> Optional[BrowserAuthResult]:
        cookie_jar = aiohttp.CookieJar()
        stored_cookies: dict[str, str] = {}
        for browser_cookie in browser_cookies:
            name = browser_cookie.get("name")
            value = browser_cookie.get("value")
            domain = str(browser_cookie.get("domain", ""))
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not domain.lstrip(".").endswith("indiegala.com")
            ):
                continue

            simple_cookie = SimpleCookie()
            simple_cookie[name] = value
            morsel = simple_cookie[name]
            if domain:
                morsel["domain"] = domain
            path = browser_cookie.get("path")
            if isinstance(path, str) and path:
                morsel["path"] = path
            if browser_cookie.get("secure"):
                morsel["secure"] = True
            cookie_jar.update_cookies(simple_cookie, URL("https://www.indiegala.com/"))
            stored_cookies[name] = value

        if not stored_cookies:
            return None

        timeout = aiohttp.ClientTimeout(total=12)
        try:
            async with aiohttp.ClientSession(
                headers={"User-Agent": "galaClient"},
                cookie_jar=cookie_jar,
                timeout=timeout,
            ) as validation_session:
                async with validation_session.get(USER_INFO_URL) as response:
                    if response.status != 200:
                        return None
                    response_text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        try:
            user_data = json.loads(response_text)
            user_id = user_data["_indiegala_user_id"]
            user_name = user_data["_indiegala_username"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

        return BrowserAuthResult(
            cookies=stored_cookies,
            user_id=str(user_id),
            user_name=str(user_name),
        )

    async def _close_browser(self) -> None:
        if self._debug_port is not None and self.browser_kind == "firefox":
            timeout = aiohttp.ClientTimeout(total=3)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(
                        f"ws://127.0.0.1:{self._debug_port}/session",
                        protocols=("webdriver-bidi",),
                    ) as socket:
                        await self._send_bidi_command(
                            socket,
                            1,
                            "session.new",
                            {"capabilities": {}},
                        )
                        await self._send_bidi_command(
                            socket,
                            2,
                            "browser.close",
                            {},
                        )
            except (
                aiohttp.ClientError,
                asyncio.TimeoutError,
                ConnectionError,
                json.JSONDecodeError,
                RuntimeError,
            ):
                pass
        elif self._debug_port is not None:
            timeout = aiohttp.ClientTimeout(total=3)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        f"http://127.0.0.1:{self._debug_port}/json/version"
                    ) as response:
                        version_data = await response.json(content_type=None)
                    websocket_url = version_data.get("webSocketDebuggerUrl")
                    if isinstance(websocket_url, str):
                        async with session.ws_connect(websocket_url) as socket:
                            await socket.send_json({"id": 99, "method": "Browser.close"})
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
                pass

        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass


class LocalBrowserAuthServer:
    def __init__(
        self,
        bridge: Optional[ExternalBrowserBridge] = None,
        install_root: Optional[str] = None,
    ):
        self.bridge = bridge or ExternalBrowserBridge()
        self.install_root = install_root or ""
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._nonce: Optional[str] = None
        self.port: Optional[int] = None
        self._stop_lock = asyncio.Lock()

    async def start(self) -> int:
        if self._runner is not None and self.port is not None:
            return self.port

        self.bridge.state = "idle"
        self.bridge.error_message = None
        self.bridge.result = None
        self._nonce = secrets.token_urlsafe(32)
        app = web.Application(client_max_size=8192)
        app.router.add_get(FORM_PATH, self._handle_form)
        app.router.add_post(OPEN_PATH, self._handle_open)
        app.router.add_get(STATUS_PATH, self._handle_status)
        app.router.add_get(COMPLETE_PATH, self._handle_complete)

        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        sockets = self._site._server.sockets if self._site._server else []
        if len(sockets) != 1:
            await self.stop()
            raise OSError("Local IndieGala auth server did not bind exactly one socket")
        address = sockets[0].getsockname()
        if address[0] != "127.0.0.1":
            await self.stop()
            raise OSError("Local IndieGala auth server did not bind to IPv4 loopback")
        self.port = int(address[1])
        logger.info("Local IndieGala auth server listening on IPv4 loopback port %s", self.port)
        return self.port

    async def stop(self) -> None:
        async with self._stop_lock:
            runner = self._runner
            site = self._site
            self._runner = None
            self._site = None
            self._nonce = None
            self.port = None

            if site is not None:
                try:
                    await asyncio.wait_for(site.stop(), timeout=2)
                except asyncio.TimeoutError:
                    logger.warning("Timed out while closing the local IndieGala auth listener")

            if runner is not None:
                try:
                    await asyncio.wait_for(runner.cleanup(), timeout=4)
                except asyncio.TimeoutError:
                    logger.warning("Timed out while closing the local IndieGala auth server")

            try:
                await asyncio.wait_for(
                    self.bridge.stop(close_browser=True),
                    timeout=7,
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out while stopping the IndieGala browser bridge")

    @property
    def result(self) -> Optional[BrowserAuthResult]:
        return self.bridge.result

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("Local IndieGala auth server is not running")
        return f"http://127.0.0.1:{self.port}/"

    @property
    def complete_url(self) -> str:
        if self.port is None:
            raise RuntimeError("Local IndieGala auth server is not running")
        return f"http://127.0.0.1:{self.port}{COMPLETE_PATH}"

    @property
    def end_uri_regex(self) -> str:
        return "^" + re.escape(self.complete_url) + "$"

    @staticmethod
    def _is_loopback_request(request: web.Request) -> bool:
        return request.remote == "127.0.0.1"

    @staticmethod
    def _response_headers() -> dict[str, str]:
        return {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'"
            ),
        }

    def _valid_nonce(self, request: web.Request) -> bool:
        submitted_nonce = request.query.get("nonce")
        return bool(
            submitted_nonce
            and self._nonce
            and secrets.compare_digest(submitted_nonce, self._nonce)
        )

    async def _handle_form(self, request: web.Request):
        if not self._is_loopback_request(request):
            raise web.HTTPForbidden()
        page = _FORM_HTML.replace("__NONCE__", json.dumps(self._nonce or ""))
        page = page.replace("__INSTALL_ROOT__", json.dumps(self.install_root))
        return web.Response(
            text=page,
            content_type="text/html",
            headers=self._response_headers(),
        )

    async def _handle_open(self, request: web.Request):
        if not self._is_loopback_request(request) or not self._valid_nonce(request):
            raise web.HTTPForbidden()
        try:
            payload = await request.json()
            self.install_root = normalize_install_root(
                payload.get("install_root", ""),
                create=True,
            )
            await self.bridge.start()
        except (json.JSONDecodeError, TypeError, InstallRootError) as error:
            logger.warning("Invalid IndieGala games folder: %s", error)
            self.bridge.state = "error"
            self.bridge.error_message = str(error)
            return web.json_response(
                {"state": "error", "message": str(error)},
                status=400,
                headers=self._response_headers(),
            )
        except (FileNotFoundError, OSError) as error:
            logger.warning("Could not start external IndieGala browser: %s", error)
            self.bridge.state = "error"
            self.bridge.error_message = str(error)
            return web.json_response(
                {"state": "error", "message": str(error)},
                status=500,
                headers=self._response_headers(),
            )
        return web.json_response(
            {
                "state": self.bridge.state,
                "browser": self.bridge.browser_name,
            },
            headers=self._response_headers(),
        )

    async def _handle_status(self, request: web.Request):
        if not self._is_loopback_request(request) or not self._valid_nonce(request):
            raise web.HTTPForbidden()
        return web.json_response(
            {
                "state": self.bridge.state,
                "message": self.bridge.error_message,
                "browser": self.bridge.browser_name,
            },
            headers=self._response_headers(),
        )

    async def _handle_complete(self, request: web.Request):
        if not self._is_loopback_request(request):
            raise web.HTTPForbidden()
        if self.bridge.state != "ready" or self.bridge.result is None:
            raise web.HTTPConflict(text="IndieGala login has not been verified")
        return web.Response(
            text=_COMPLETE_HTML,
            content_type="text/html",
            headers=self._response_headers(),
        )
