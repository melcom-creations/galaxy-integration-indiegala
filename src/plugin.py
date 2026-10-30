import os
import sys


# Add the bundled modules directory to sys.path before importing dependencies.
def _resolve_modules_dir():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate in ("modules", "Modules"):
        candidate_path = os.path.join(base_dir, candidate)
        if os.path.isdir(candidate_path):
            return candidate_path
    try:
        for entry in os.listdir(base_dir):
            if entry.lower() == "modules":
                candidate_path = os.path.join(base_dir, entry)
                if os.path.isdir(candidate_path):
                    return candidate_path
    except OSError:
        pass
    return os.path.join(base_dir, "modules")


def _normalized_path(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


modules_dir = _resolve_modules_dir()
if os.path.isdir(modules_dir):
    resolved_modules_dir = os.path.abspath(modules_dir)
    normalized_sys_path = {_normalized_path(path) for path in sys.path}
    if _normalized_path(resolved_modules_dir) not in normalized_sys_path:
        sys.path.insert(0, resolved_modules_dir)


# Keep bundled asynchronous dependencies independent of optional native modules.
os.environ["AIOHTTP_NO_EXTENSIONS"] = "1"
os.environ["YARL_NO_EXTENSIONS"] = "1"
os.environ["MULTIDICT_NO_EXTENSIONS"] = "1"
os.environ["PROPCACHE_NO_EXTENSIONS"] = "1"
os.environ["FROZENLIST_NO_EXTENSIONS"] = "1"

import json
import asyncio
import base64
import logging
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from galaxy.api.plugin import Plugin, create_and_run_plugin
from galaxy.api.consts import Platform, LicenseType, LocalGameState, OSCompatibility
from galaxy.api.types import NextStep, Authentication, Game, LicenseInfo, LocalGame
from galaxy.api.errors import (
    AuthenticationRequired, InvalidCredentials, BackendError,
    BackendNotAvailable, BackendTimeout, NetworkError,
)
from galaxy.api.jsonrpc import ApplicationError

from browser_auth import LocalBrowserAuthServer
from game_time import GameTimeSyncResult, GameTimeTracker
from http_client import HTTPClient
from local import GameNotInstalledError, IndieGalaLocalGameManager
from local_settings import InstallRootError, load_install_root, save_install_root

with open(Path(__file__).parent / 'manifest.json', 'r') as f:
    __version__ = json.load(f)['version']

SHOWCASE_API = 'https://www.indiegala.com/login_new/user_info'
LIBRARY_CACHE_KEY = "indiegala_library_v1"
LIBRARY_ACCOUNT_CACHE_KEY = "indiegala_library_account_v1"
GAME_TIME_CACHE_KEY = "indiegala_game_time_v1"
LOCAL_STATUS_REFRESH_SECONDS = 1.0
ACCOUNT_RETRY_DELAYS_SECONDS = (1.0, 2.0)
ACCOUNT_REQUEST_TIMEOUT_SECONDS = 5.0
ACCOUNT_OPERATION_TIMEOUT_SECONDS = 25.0
SESSION_CHECK_TIMEOUT_SECONDS = 6.0


def _response_error(path, expected, value=None, *, missing=False, index=None):
    # Record schema information, never account fields or response contents.
    details = {
        "reason": "invalid_schema",
        "path": path,
        "expected": expected,
        "actual": "missing" if missing else type(value).__name__,
    }
    if index is not None:
        details["index"] = index
    return BackendError("IndieGala returned incomplete or invalid data; try again later", details)


def _parse_user_response(text):
    """Do not mistake an unavailable account endpoint for an expired session."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as error:
        raise BackendError(
            "IndieGala returned an invalid account response; try again later",
            {"reason": "invalid_json", "path": "$", "expected": "JSON object"},
        ) from error
    if not isinstance(data, dict):
        raise _response_error("$", "object", data)
    if data.get("status") == "failure":
        raise BackendError(
            "IndieGala could not provide account data; try again later",
            {"reason": "api_failure", "path": "$"},
        )
    return data


def _parse_identity_response(text):
    data = _parse_user_response(text)
    user_id = data.get('_indiegala_user_id')
    username = data.get('_indiegala_username')
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, (str, int))
        or not str(user_id).strip()
    ):
        raise _response_error(
            "_indiegala_user_id", "nonempty string or integer", user_id,
            missing='_indiegala_user_id' not in data,
        )
    if not isinstance(username, str) or not username.strip():
        raise _response_error(
            "_indiegala_username", "nonempty string", username,
            missing='_indiegala_username' not in data,
        )
    return Authentication(str(user_id), username)


def _parse_library_response(text):
    data = _parse_user_response(text)
    path = []
    for field in ('showcase_content', 'content', 'user_collection'):
        path.append(field)
        value = data.get(field)
        expected_type = list if field == 'user_collection' else dict
        if not isinstance(value, expected_type):
            raise _response_error(
                '.'.join(path), expected_type.__name__, value, missing=field not in data,
            )
        data = value

    result = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise _response_error('.'.join(path) + '[]', "object", item, index=index)
        for field in ('prod_slugged_name', 'prod_name'):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                raise _response_error(
                    '.'.join(path) + '[].' + field, "nonempty string", value,
                    missing=field not in item, index=index,
                )
        result.append(Game(
            game_id=item['prod_slugged_name'],
            game_title=item['prod_name'],
            license_info=LicenseInfo(LicenseType.SinglePurchase),
            dlcs=[],
        ))
    return result


def write_crash_log(err_msg):
    if isinstance(sys.exc_info()[1], ApplicationError):
        # Expected service/authentication errors belong in Galaxy's regular log.
        return
    try:
        crash_file = Path(__file__).parent / "crash_log.txt"
        with open(crash_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== CRASH LOG: {err_msg} ===\n")
            traceback.print_exc(file=f)
            f.write("=======================================\n")
    except Exception:
        pass


class IndieGalaPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(
            Platform.IndieGala,
            __version__,
            reader,
            writer,
            token
        )
        self.http_client = HTTPClient(self.store_credentials)
        self.install_root = load_install_root()
        self.browser_auth = LocalBrowserAuthServer(install_root=self.install_root)
        self._owned_game_titles = {}
        self._authenticated_user_id = None
        self._library_cache_account_id = None
        self._has_complete_library_cache = False
        self._local_manager = IndieGalaLocalGameManager(
            lambda: self.install_root,
            self._library_for_local_detection,
        )
        self._game_time = GameTimeTracker()
        self._local_statuses = {}
        self._local_games_initialized = False
        self._local_refresh_task = None
        self._last_local_refresh = 0.0
        self._install_dialog_lock = asyncio.Lock()

    def handshake_complete(self):
        cached_game_time = self.persistent_cache.get(GAME_TIME_CACHE_KEY)
        self._game_time.load(
            cached_game_time if isinstance(cached_game_time, str) else None
        )
        self._load_library_cache()
        logging.info(
            "IndieGala plugin handshake completed, games folder=%s",
            self.install_root or "not configured",
        )

    async def shutdown(self):
        try:
            self._apply_game_time_sync(
                self._game_time.checkpoint_active(),
                send_updates=False,
            )
        except Exception:
            logging.exception("Unable to checkpoint local IndieGala Game-Time during shutdown")
        try:
            await asyncio.wait_for(self.browser_auth.stop(), timeout=12)
        except asyncio.TimeoutError:
            logging.warning("IndieGala browser authentication shutdown timed out")
        try:
            await asyncio.wait_for(self.http_client.close(), timeout=5)
        except asyncio.TimeoutError:
            logging.warning("IndieGala HTTP client shutdown timed out")

    async def _build_browser_auth_step(self):
        await self.browser_auth.stop()
        self.browser_auth.install_root = self.install_root or ""
        await self.browser_auth.start()
        logging.info("Starting IndieGala authentication through an external browser")
        return NextStep(
            "web_session",
            {
                "window_title": "Connect IndieGala",
                "window_width": 620,
                "window_height": 450,
                "start_uri": self.browser_auth.base_url,
                "end_uri_regex": self.browser_auth.end_uri_regex,
            },
        )

    async def authenticate(self, stored_credentials=None):
        try:
            self._authenticated_user_id = None
            if not stored_credentials:
                return await self._build_browser_auth_step()
            if not self.install_root:
                logging.info("IndieGala games folder must be configured during connection")
                return await self._build_browser_auth_step()
            self.http_client.update_cookies(stored_credentials)
            try:
                return await self.get_user_info()
            except AuthenticationRequired:
                self.http_client.clear_cookies()
                return await self._build_browser_auth_step()
        except Exception as e:
            write_crash_log("Fehler in authenticate")
            raise

    async def pass_login_credentials(self, step, credentials, cookies):
        del step, credentials, cookies
        try:
            browser_result = self.browser_auth.result
            if browser_result is None:
                raise InvalidCredentials("External IndieGala login was not completed")

            try:
                self.install_root = save_install_root(self.browser_auth.install_root)
            except InstallRootError as error:
                raise InvalidCredentials(str(error)) from error

            self.http_client.clear_cookies()
            self.http_client.update_cookies(browser_result.cookies)
            authentication = await self.get_user_info()
            if (
                authentication.user_id != browser_result.user_id
                or authentication.user_name != browser_result.user_name
            ):
                raise InvalidCredentials("IndieGala account verification changed unexpectedly")
            return authentication
        except Exception as e:
            write_crash_log("Fehler in pass_login_credentials")
            raise
        finally:
            await self.browser_auth.stop()

    async def get_owned_games(self):
        try:
            result = await self._fetch_account_data("library", _parse_library_response)
            self._owned_game_titles = {
                game.game_id: game.game_title for game in result
            }
            self._store_library_cache()
            return result
        except (BackendError, BackendNotAvailable, BackendTimeout, NetworkError) as error:
            if error.data.get("session_verified") is True:
                cached = self._cached_library_for_current_account()
                if cached is not None:
                    logging.warning(
                        "IndieGala library refresh unavailable; retaining %d previously verified games. "
                        "New library changes will be imported after a successful refresh.",
                        len(cached),
                    )
                    return cached
            raise
        except Exception as e:
            write_crash_log("Fehler in get_owned_games")
            raise

    async def get_user_info(self):
        try:
            authentication = await self._fetch_account_data("account", _parse_identity_response)
            self._authenticated_user_id = authentication.user_id
            return authentication
        except Exception as e:
            write_crash_log("Fehler in get_user_info")
            raise

    async def _fetch_account_data(self, operation, parser):
        try:
            async with asyncio.timeout(ACCOUNT_OPERATION_TIMEOUT_SECONDS):
                return await self._fetch_account_data_with_retries(operation, parser)
        except asyncio.TimeoutError as error:
            logging.warning("IndieGala %s recovery exceeded its time limit", operation)
            raise BackendTimeout("IndieGala recovery timed out; try again later") from error

    async def _fetch_account_data_with_retries(self, operation, parser):
        attempts = len(ACCOUNT_RETRY_DELAYS_SECONDS) + 1
        for attempt in range(1, attempts + 1):
            try:
                text = await asyncio.wait_for(
                    self.http_client.get(SHOWCASE_API),
                    timeout=ACCOUNT_REQUEST_TIMEOUT_SECONDS,
                )
                result = parser(text)
                if attempt > 1:
                    logging.info("IndieGala %s recovered on attempt %d/%d", operation, attempt, attempts)
                return result
            except (BackendError, BackendNotAvailable, BackendTimeout, NetworkError, asyncio.TimeoutError) as error:
                if isinstance(error, asyncio.TimeoutError):
                    failure = BackendTimeout("IndieGala account request timed out")
                else:
                    failure = error
                details = failure.data
                status = details.get("http_status")
                logging.warning(
                    "IndieGala %s attempt %d/%d failed: error=%s reason=%s "
                    "path=%s expected=%s actual=%s index=%s http_status=%s",
                    operation, attempt, attempts, type(failure).__name__,
                    details.get("reason", "request_failed"), details.get("path", "-"),
                    details.get("expected", "-"), details.get("actual", "-"),
                    details.get("index", "-"), status if status is not None else "-",
                )
                retryable = status is None or status in (408, 425) or 500 <= status < 600
                if retryable and attempt < attempts:
                    await asyncio.sleep(ACCOUNT_RETRY_DELAYS_SECONDS[attempt - 1])
                    continue
                # Retention requires fresh session confirmation and a complete cache
                # bound to the authenticated account; malformed data is never merged.
                if await self._check_session_after_backend_error() is True:
                    failure.data["session_verified"] = True
                raise failure

    async def _check_session_after_backend_error(self):
        try:
            authenticated = await asyncio.wait_for(
                self.http_client.is_session_authenticated(),
                timeout=SESSION_CHECK_TIMEOUT_SECONDS,
            )
        except AuthenticationRequired:
            raise
        except (ApplicationError, asyncio.TimeoutError) as error:
            # Preserve the original failure when session verification is unavailable.
            logging.warning("IndieGala session verification unavailable: error=%s", type(error).__name__)
            return
        if authenticated is False:
            logging.info("IndieGala website confirms that the stored session is no longer logged in")
            raise AuthenticationRequired()
        logging.info("IndieGala session is still logged in; account/library service is unavailable")
        return authenticated

    def _cached_library_for_current_account(self):
        account_id = getattr(self, '_authenticated_user_id', None)
        if (
            not account_id
            or not getattr(self, '_has_complete_library_cache', False)
            or getattr(self, '_library_cache_account_id', None) != account_id
        ):
            return None
        return [
            Game(
                game_id=game_id, game_title=title,
                license_info=LicenseInfo(LicenseType.SinglePurchase), dlcs=[],
            )
            for game_id, title in self._owned_game_titles.items()
        ]

    def _store_library_cache(self):
        self.persistent_cache.pop("credentials", None)
        self.persistent_cache[LIBRARY_CACHE_KEY] = json.dumps(
            self._owned_game_titles,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        account_id = self._authenticated_user_id
        if account_id:
            self.persistent_cache[LIBRARY_ACCOUNT_CACHE_KEY] = account_id
        else:
            self.persistent_cache.pop(LIBRARY_ACCOUNT_CACHE_KEY, None)
        self._library_cache_account_id = account_id
        self._has_complete_library_cache = True
        self.push_cache()

    def _load_library_cache(self):
        cached = self.persistent_cache.get(LIBRARY_CACHE_KEY)
        if not isinstance(cached, str):
            return
        try:
            payload = json.loads(cached)
        except (TypeError, ValueError, json.JSONDecodeError):
            logging.warning("Ignoring invalid IndieGala library cache")
            return
        if not isinstance(payload, dict):
            return
        self._owned_game_titles = {
            str(game_id): str(title)
            for game_id, title in payload.items()
            if isinstance(game_id, str)
            and game_id.strip()
            and isinstance(title, str)
            and title.strip()
        }
        # Unbound legacy caches remain available for local detection, but cannot
        # be used for retention until a fresh successful import binds the account.
        account_id = self.persistent_cache.get(LIBRARY_ACCOUNT_CACHE_KEY)
        self._library_cache_account_id = account_id if isinstance(account_id, str) and account_id.strip() else None
        self._has_complete_library_cache = len(self._owned_game_titles) == len(payload)

    def _library_for_local_detection(self):
        if not self._owned_game_titles:
            self._load_library_cache()
        return dict(self._owned_game_titles)

    async def get_local_games(self):
        local_games = await asyncio.to_thread(
            self._local_manager.get_local_games,
            force_refresh=True,
        )
        self._local_statuses = {
            game.game_id: game.local_game_state for game in local_games
        }
        self._local_games_initialized = True
        logging.info("Prepared IndieGala local game import: installed games=%d", len(local_games))
        return local_games

    async def launch_game(self, game_id):
        try:
            result = await self._local_manager.launch(game_id)
        except GameNotInstalledError as error:
            logging.warning("Launch rejected because IndieGala game is not installed: %s", game_id)
            raise BackendError("IndieGala game is not installed") from error
        except OSError as error:
            logging.exception("Unable to launch IndieGala game %s", game_id)
            raise BackendError("IndieGala game launch failed") from error

        if result.process is not None:
            state = LocalGameState.Installed | LocalGameState.Running
            self.update_local_game_status(LocalGame(game_id, state))
            self._local_statuses[game_id] = state
            self._local_games_initialized = True

    async def install_game(self, game_id):
        del game_id
        if self._install_dialog_lock.locked():
            logging.info("Ignoring duplicate IndieGala games-folder dialog request")
            return

        async with self._install_dialog_lock:
            current_path = self.install_root or r"C:\Games\IndieGala"
            try:
                selected_path = await asyncio.to_thread(
                    self._show_install_root_dialog,
                    current_path,
                )
                if selected_path is None:
                    logging.info("IndieGala games-folder change was cancelled")
                    return
                self.install_root = await asyncio.to_thread(
                    save_install_root,
                    selected_path,
                )
                self.browser_auth.install_root = self.install_root
                await self._refresh_local_game_statuses(force_refresh=True)
                logging.info(
                    "IndieGala games folder changed through Install action: %s",
                    self.install_root,
                )
            except InstallRootError as error:
                logging.warning("Invalid IndieGala games folder: %s", error)
                raise BackendError(str(error)) from error
            except OSError as error:
                logging.exception("Unable to open the IndieGala games-folder dialog")
                raise BackendError("The IndieGala games-folder dialog could not be opened") from error

    async def get_os_compatibility(self, game_id, context):
        del game_id, context
        # The local installation workflow is Windows-only. Reporting this
        # explicitly also keeps Galaxy's Install context action available when
        # GamesDB has no platform metadata for an IndieGala release.
        return OSCompatibility.Windows

    @staticmethod
    def _show_install_root_dialog(current_path):
        powershell = shutil.which("powershell.exe")
        dialog_script = Path(__file__).parent / "install_location_dialog.ps1"
        if powershell is None:
            raise OSError("Windows PowerShell was not found")
        if not dialog_script.is_file():
            raise OSError("The IndieGala games-folder dialog script is missing")

        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(dialog_script),
                "-CurrentPath",
                current_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 2:
            return None
        if result.returncode != 0:
            error_message = result.stderr.decode("utf-8", errors="replace").strip()
            raise OSError(error_message or "The games-folder dialog failed")

        output_lines = result.stdout.decode("ascii", errors="ignore").splitlines()
        encoded_path = next((line.strip() for line in reversed(output_lines) if line.strip()), "")
        if not encoded_path:
            raise OSError("The games-folder dialog returned no path")
        try:
            return base64.b64decode(encoded_path, validate=True).decode("utf-8")
        except (ValueError, UnicodeError) as error:
            raise OSError("The games-folder dialog returned an invalid path") from error

    def tick(self):
        if not self._local_games_initialized:
            return
        if self._local_refresh_task is not None and not self._local_refresh_task.done():
            return
        now = time.monotonic()
        if now - self._last_local_refresh < LOCAL_STATUS_REFRESH_SECONDS:
            return
        self._last_local_refresh = now
        self._local_refresh_task = self.create_task(
            self._refresh_local_game_statuses(),
            "IndieGala local game status refresh",
        )

    async def _refresh_local_game_statuses(self, *, force_refresh=False):
        detected_games = await asyncio.to_thread(
            self._local_manager.get_detected_local_games,
            force_refresh=force_refresh,
        )
        current_games = [detected.as_local_game() for detected in detected_games]
        current = {game.game_id: game.local_game_state for game in current_games}
        all_game_ids = set(self._local_statuses) | set(current)
        for game_id in all_game_ids:
            previous = self._local_statuses.get(game_id, LocalGameState.None_)
            state = current.get(game_id, LocalGameState.None_)
            if state != previous:
                self.update_local_game_status(LocalGame(game_id, state))
                logging.info(
                    "IndieGala local state changed: %s, state=%s",
                    game_id,
                    state,
                )
        self._local_statuses = current
        self._apply_game_time_sync(self._game_time.sync(detected_games))

    async def get_game_time(self, game_id, context):
        del context
        return self._game_time.get_game_time(game_id)

    def game_times_import_complete(self):
        logging.info("IndieGala local Game-Time import completed")

    def _apply_game_time_sync(self, result: GameTimeSyncResult, *, send_updates=True):
        if result.cache_changed:
            self.persistent_cache.pop("credentials", None)
            self.persistent_cache[GAME_TIME_CACHE_KEY] = self._game_time.serialize()
            self.push_cache()
        if send_updates:
            for game_time in result.updates:
                self.update_game_time(game_time)


def main():
    create_and_run_plugin(IndieGalaPlugin, sys.argv)


if __name__ == "__main__":
    main()
