from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

import psutil
from galaxy.api.consts import LocalGameState
from galaxy.api.types import LocalGame


logger = logging.getLogger(__name__)

INSTALLATION_REFRESH_SECONDS = 10.0
PROCESS_POLL_SECONDS = 1.0
PROCESS_START_TIMEOUT_SECONDS = 10.0
MAX_EXECUTABLE_SCAN_DEPTH = 3

_BANNED_EXECUTABLES = re.compile(
    r"^(?:unins|uninstall|setup|installer|updater|update|patcher|repair|"
    r"crash(?:reporter|handler)?|unitycrashhandler|dxsetup|vcredist|vc_redist|"
    r"dotnet|physx)",
    re.IGNORECASE,
)
_BANNED_DIRECTORIES = {
    "__installer",
    "_commonredist",
    "commonredist",
    "directx",
    "dotnet",
    "installer",
    "redist",
    "support",
    "vcredist",
}


class GameNotInstalledError(RuntimeError):
    pass


class LaunchTargetError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstalledIndieGalaGame:
    game_id: str
    title: str
    install_dir: str
    launch_executable: str
    working_directory: str


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    executable: str
    create_time: float | None = None


@dataclass(frozen=True)
class DetectedLocalGame:
    game: InstalledIndieGalaGame
    local_game_state: LocalGameState
    process: ProcessInfo | None

    def as_local_game(self) -> LocalGame:
        return LocalGame(self.game.game_id, self.local_game_state)


@dataclass(frozen=True)
class LaunchResult:
    game: InstalledIndieGalaGame
    process: ProcessInfo | None
    started: bool


class PsutilProcessProvider:
    def snapshot(self) -> dict[int, ProcessInfo]:
        processes: dict[int, ProcessInfo] = {}
        for process in psutil.process_iter(("pid", "ppid", "exe", "create_time")):
            try:
                executable = process.info.get("exe")
                if not executable:
                    continue
                info = ProcessInfo(
                    pid=int(process.info["pid"]),
                    ppid=int(process.info.get("ppid") or 0),
                    executable=str(executable),
                    create_time=(
                        float(process.info["create_time"])
                        if process.info.get("create_time") is not None
                        else None
                    ),
                )
                processes[info.pid] = info
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
                continue
        return processes


class IndieGalaInstallationDetector:
    def detect(
        self,
        install_root: str | None,
        library: dict[str, str],
        *,
        log_summary: bool = True,
    ) -> list[InstalledIndieGalaGame]:
        if not install_root or not os.path.isdir(install_root):
            if log_summary:
                logger.info("No valid IndieGala games folder is configured")
            return []
        if Path(install_root).name.casefold() != "indiegala":
            if log_summary:
                logger.warning("Configured games folder is not named IndieGala: %s", install_root)
            return []

        aliases = _library_aliases(library)
        installed: list[InstalledIndieGalaGame] = []
        try:
            game_directories = sorted(
                (item for item in Path(install_root).iterdir() if item.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        except OSError as error:
            logger.warning("Unable to scan IndieGala games folder: %s", error)
            return []

        for game_directory in game_directories:
            mapped = _map_game_directory(game_directory.name, aliases)
            if mapped is None:
                if log_summary:
                    logger.debug(
                        "Ignoring unmatched IndieGala game directory: %s",
                        game_directory,
                    )
                continue
            game_id, title = mapped
            try:
                executable = _resolve_launch_executable(
                    game_directory,
                    game_id,
                    title,
                )
            except LaunchTargetError as error:
                if log_summary:
                    logger.warning(
                        "IndieGala installation has no safe launch target: %s (%s)",
                        title,
                        error,
                    )
                continue

            installed.append(
                InstalledIndieGalaGame(
                    game_id=game_id,
                    title=title,
                    install_dir=str(game_directory.resolve()),
                    launch_executable=str(executable.resolve()),
                    working_directory=str(game_directory.resolve()),
                )
            )
            if log_summary:
                logger.info(
                    "Detected installed IndieGala game: %s -> %s",
                    title,
                    executable,
                )

        if log_summary:
            logger.info(
                "Scanned %d IndieGala directories and detected %d installed games",
                len(game_directories),
                len(installed),
            )
        return installed


class IndieGalaLocalGameManager:
    def __init__(
        self,
        install_root_provider: Callable[[], str | None],
        library_provider: Callable[[], dict[str, str]],
        detector: IndieGalaInstallationDetector | None = None,
        process_provider: Any = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        installation_refresh_seconds: float = INSTALLATION_REFRESH_SECONDS,
        process_start_timeout_seconds: float = PROCESS_START_TIMEOUT_SECONDS,
    ):
        self._install_root_provider = install_root_provider
        self._library_provider = library_provider
        self._detector = detector or IndieGalaInstallationDetector()
        self._process_provider = process_provider or PsutilProcessProvider()
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._clock = clock
        self._installation_refresh_seconds = installation_refresh_seconds
        self._process_start_timeout_seconds = process_start_timeout_seconds
        self._installed: dict[str, InstalledIndieGalaGame] = {}
        self._last_installation_refresh: float | None = None

    def refresh_installations(self, *, force: bool = False) -> list[InstalledIndieGalaGame]:
        now = self._clock()
        refresh_due = (
            force
            or self._last_installation_refresh is None
            or now - self._last_installation_refresh >= self._installation_refresh_seconds
        )
        if refresh_due:
            games = self._detector.detect(
                self._install_root_provider(),
                self._library_provider(),
                log_summary=force or self._last_installation_refresh is None,
            )
            self._installed = {game.game_id: game for game in games}
            self._last_installation_refresh = now
        return list(self._installed.values())

    def get_local_games(self, *, force_refresh: bool = False) -> list[LocalGame]:
        return [
            detected.as_local_game()
            for detected in self.get_detected_local_games(force_refresh=force_refresh)
        ]

    def get_detected_local_games(
        self,
        *,
        force_refresh: bool = False,
    ) -> list[DetectedLocalGame]:
        games = self.refresh_installations(force=force_refresh)
        snapshot = self._process_provider.snapshot()
        detected_games: list[DetectedLocalGame] = []
        for game in games:
            state = LocalGameState.Installed
            process = _find_running_process(game, snapshot)
            if process is not None:
                state |= LocalGameState.Running
            detected_games.append(DetectedLocalGame(game, state, process))
        return detected_games

    async def launch(self, game_id: str) -> LaunchResult:
        game = next(
            (item for item in self.refresh_installations(force=True) if item.game_id == game_id),
            None,
        )
        if game is None:
            raise GameNotInstalledError(game_id)

        before = self._process_provider.snapshot()
        running = _find_running_process(game, before)
        if running is not None:
            return LaunchResult(game, running, False)

        logger.info("Launch requested for IndieGala game %s", game_id)
        launched = self._popen_factory(
            [game.launch_executable],
            cwd=game.working_directory,
            shell=False,
        )

        deadline = self._clock() + self._process_start_timeout_seconds
        while True:
            snapshot = self._process_provider.snapshot()
            running = _find_started_game_process(
                game,
                snapshot,
                before_pids=set(before),
                launcher_pid=getattr(launched, "pid", None),
            )
            if running is not None:
                logger.info(
                    "Actual IndieGala game process detected for %s (pid=%d)",
                    game_id,
                    running.pid,
                )
                return LaunchResult(game, running, True)
            if self._clock() >= deadline:
                logger.warning(
                    "IndieGala launch completed but no durable game process was identified: %s",
                    game_id,
                )
                return LaunchResult(game, None, True)
            await self._sleep(PROCESS_POLL_SECONDS)


def _normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(character for character in decomposed.casefold() if character.isalnum())


def _library_aliases(library: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    aliases: dict[str, list[tuple[str, str]]] = {}
    for game_id, title in library.items():
        if not isinstance(game_id, str) or not game_id.strip():
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        game = (game_id.strip(), title.strip())
        for value in {game[0], game[1]}:
            normalized = _normalize_name(value)
            if normalized:
                aliases.setdefault(normalized, []).append(game)
    return aliases


def _map_game_directory(
    directory_name: str,
    aliases: dict[str, list[tuple[str, str]]],
) -> tuple[str, str] | None:
    matches = aliases.get(_normalize_name(directory_name), [])
    unique = {game_id: title for game_id, title in matches}
    if len(unique) != 1:
        return None
    return next(iter(unique.items()))


def _resolve_launch_executable(
    install_dir: Path,
    game_id: str,
    title: str,
) -> Path:
    candidates: list[tuple[float, int, int, str, Path]] = []
    aliases = {_normalize_name(game_id), _normalize_name(title), _normalize_name(install_dir.name)}
    aliases.discard("")

    for current_root, directories, files in os.walk(install_dir):
        current_path = Path(current_root)
        depth = len(current_path.relative_to(install_dir).parts)
        if depth >= MAX_EXECUTABLE_SCAN_DEPTH:
            directories[:] = []
        else:
            directories[:] = [
                name for name in directories if name.casefold() not in _BANNED_DIRECTORIES
            ]

        for filename in files:
            candidate = current_path / filename
            if candidate.suffix.casefold() != ".exe":
                continue
            if _BANNED_EXECUTABLES.match(candidate.stem):
                continue
            normalized_stem = _normalize_name(candidate.stem)
            similarity = max(
                (SequenceMatcher(None, normalized_stem, alias).ratio() for alias in aliases),
                default=0.0,
            )
            score = similarity * 100.0
            if normalized_stem in aliases:
                score += 200.0
            if normalized_stem in {"game", "start", "play"}:
                score += 25.0
            elif "launcher" in normalized_stem:
                score += 10.0
            score -= depth * 4.0
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            candidates.append((score, -depth, size, str(candidate).casefold(), candidate))

    if not candidates:
        raise LaunchTargetError("no suitable executable was found")
    candidates.sort(reverse=True)
    best_score, _depth, _size, _path_key, best = candidates[0]
    if len(candidates) > 1 and best_score < 40.0:
        raise LaunchTargetError("multiple executables were found but none matched the game name")
    return best


def _find_running_process(
    game: InstalledIndieGalaGame,
    snapshot: dict[int, ProcessInfo],
) -> ProcessInfo | None:
    expected = _normalize_path(game.launch_executable)
    for process in snapshot.values():
        if _normalize_path(process.executable) == expected:
            return process

    install_dir = _normalize_path(game.install_dir)
    candidates = []
    for process in snapshot.values():
        executable = _normalize_path(process.executable)
        try:
            inside_install = os.path.commonpath((install_dir, executable)) == install_dir
        except ValueError:
            inside_install = False
        if inside_install and not _BANNED_EXECUTABLES.match(Path(executable).stem):
            candidates.append(process)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _find_started_game_process(
    game: InstalledIndieGalaGame,
    snapshot: dict[int, ProcessInfo],
    *,
    before_pids: set[int],
    launcher_pid: int | None,
) -> ProcessInfo | None:
    exact = _find_running_process(game, snapshot)
    if exact is not None and exact.pid not in before_pids:
        return exact

    install_dir = _normalize_path(game.install_dir)
    candidates: list[ProcessInfo] = []
    for process in snapshot.values():
        if process.pid in before_pids:
            continue
        executable = _normalize_path(process.executable)
        try:
            inside_install = os.path.commonpath((install_dir, executable)) == install_dir
        except ValueError:
            inside_install = False
        if not inside_install or _BANNED_EXECUTABLES.match(Path(executable).stem):
            continue
        if launcher_pid is None or process.pid == launcher_pid or _is_descendant(
            process.pid,
            launcher_pid,
            snapshot,
        ):
            candidates.append(process)
    if len(candidates) == 1:
        return candidates[0]
    return None


def _is_descendant(
    process_pid: int,
    ancestor_pid: int,
    snapshot: dict[int, ProcessInfo],
) -> bool:
    visited: set[int] = set()
    current = snapshot.get(process_pid)
    while current is not None and current.ppid and current.ppid not in visited:
        if current.ppid == ancestor_pid:
            return True
        visited.add(current.ppid)
        current = snapshot.get(current.ppid)
    return False


def _normalize_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(value.rstrip("\\/")))
