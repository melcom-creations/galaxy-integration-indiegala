from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from galaxy.api.consts import LocalGameState
from galaxy.api.types import GameTime

from local import DetectedLocalGame, ProcessInfo


logger = logging.getLogger(__name__)

GAME_TIME_CACHE_VERSION = 1
GAME_TIME_CHECKPOINT_SECONDS = 30.0
PROCESS_CREATE_TIME_TOLERANCE = 0.01


@dataclass
class PersistedActiveProcess:
    pid: int
    create_time: float | None
    checkpoint_monotonic: float
    checkpoint_wall_time: float


@dataclass
class GameTimeRecord:
    total_seconds: float = 0.0
    last_played_time: int | None = None
    active_process: PersistedActiveProcess | None = None


@dataclass
class GameTimeSession:
    game_id: str
    pid: int
    create_time: float | None
    checkpoint_monotonic: float
    started_wall_time: float
    session_seconds: float = 0.0


@dataclass(frozen=True)
class GameTimeSyncResult:
    cache_changed: bool
    updates: tuple[GameTime, ...]


class GameTimeTracker:
    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        checkpoint_seconds: float = GAME_TIME_CHECKPOINT_SECONDS,
    ):
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._checkpoint_seconds = checkpoint_seconds
        self._records: dict[str, GameTimeRecord] = {}
        self._sessions: dict[str, GameTimeSession] = {}
        self._last_reported_minutes: dict[str, int] = {}

    def load(self, value: str | None) -> None:
        self._records = {}
        self._sessions = {}
        self._last_reported_minutes = {}
        if value is None:
            logger.info("No local IndieGala Game-Time cache exists yet")
            return
        try:
            payload = json.loads(value)
            self._records = _decode_records(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Ignoring invalid local IndieGala Game-Time cache")
            return
        logger.info("Loaded local IndieGala Game-Time cache for %d games", len(self._records))

    def serialize(self) -> str:
        games: dict[str, dict[str, Any]] = {}
        for game_id, record in self._records.items():
            encoded: dict[str, Any] = {
                "total_seconds": record.total_seconds,
                "last_played_time": record.last_played_time,
            }
            if record.active_process is not None:
                encoded["active_process"] = {
                    "pid": record.active_process.pid,
                    "create_time": record.active_process.create_time,
                    "checkpoint_monotonic": record.active_process.checkpoint_monotonic,
                    "checkpoint_wall_time": record.active_process.checkpoint_wall_time,
                }
            games[game_id] = encoded
        return json.dumps(
            {"version": GAME_TIME_CACHE_VERSION, "games": games},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def sync(self, detected_games: Iterable[DetectedLocalGame]) -> GameTimeSyncResult:
        now_monotonic = self._monotonic()
        now_wall = self._wall_time()
        running = {
            detected.game.game_id: (detected, detected.process)
            for detected in detected_games
            if LocalGameState.Running in detected.local_game_state
            and detected.process is not None
        }
        updates: list[GameTime] = []
        cache_changed = False

        for game_id, session in list(self._sessions.items()):
            current = running.get(game_id)
            if current is not None and _same_runtime_process(session, current[1]):
                continue
            updates.append(self._finish_session(session, now_monotonic, now_wall))
            cache_changed = True

        for game_id, (detected, process) in running.items():
            if game_id in self._sessions:
                continue
            record = self._records.setdefault(game_id, GameTimeRecord())
            active = record.active_process
            if active is not None and _can_resume_process(active, process):
                checkpoint = active.checkpoint_monotonic
                if checkpoint < 0 or checkpoint > now_monotonic:
                    checkpoint = now_monotonic
                session = GameTimeSession(
                    game_id=game_id,
                    pid=process.pid,
                    create_time=process.create_time,
                    checkpoint_monotonic=checkpoint,
                    started_wall_time=active.checkpoint_wall_time,
                )
                logger.info("IndieGala Game-Time session resumed: %s", game_id)
            else:
                session = GameTimeSession(
                    game_id=game_id,
                    pid=process.pid,
                    create_time=process.create_time,
                    checkpoint_monotonic=now_monotonic,
                    started_wall_time=now_wall,
                )
                record.active_process = _active_process_from_session(
                    session,
                    now_monotonic,
                    now_wall,
                )
                cache_changed = True
                logger.info("IndieGala Game-Time session started: %s", game_id)
            self._sessions[game_id] = session
            self._last_reported_minutes.setdefault(
                game_id,
                math.floor(record.total_seconds / 60.0),
            )

        for game_id, record in self._records.items():
            if (
                record.active_process is not None
                and game_id not in running
                and game_id not in self._sessions
            ):
                record.last_played_time = max(
                    record.last_played_time or 0,
                    int(record.active_process.checkpoint_wall_time),
                )
                record.active_process = None
                cache_changed = True

        for game_id, session in list(self._sessions.items()):
            record = self._records[game_id]
            elapsed = max(0.0, now_monotonic - session.checkpoint_monotonic)
            visible_minutes = math.floor((record.total_seconds + elapsed) / 60.0)
            if self._last_reported_minutes.get(game_id) != visible_minutes:
                updates.append(GameTime(game_id, visible_minutes, int(now_wall)))
                self._last_reported_minutes[game_id] = visible_minutes
            if elapsed >= self._checkpoint_seconds:
                self._checkpoint_session(session, record, now_monotonic, now_wall)
                cache_changed = True

        return GameTimeSyncResult(cache_changed, tuple(updates))

    def checkpoint_active(self) -> GameTimeSyncResult:
        now_monotonic = self._monotonic()
        now_wall = self._wall_time()
        updates: list[GameTime] = []
        cache_changed = False
        for game_id, session in self._sessions.items():
            record = self._records[game_id]
            elapsed = max(0.0, now_monotonic - session.checkpoint_monotonic)
            if elapsed <= 0:
                continue
            self._checkpoint_session(session, record, now_monotonic, now_wall)
            cache_changed = True
            minutes = math.floor(record.total_seconds / 60.0)
            if self._last_reported_minutes.get(game_id) != minutes:
                updates.append(GameTime(game_id, minutes, int(now_wall)))
                self._last_reported_minutes[game_id] = minutes
        return GameTimeSyncResult(cache_changed, tuple(updates))

    def get_game_time(self, game_id: str) -> GameTime:
        record = self._records.get(game_id)
        if record is None:
            return GameTime(game_id, None, None)

        total_seconds = record.total_seconds
        session = self._sessions.get(game_id)
        if session is not None:
            total_seconds += max(0.0, self._monotonic() - session.checkpoint_monotonic)
            last_played_time = int(self._wall_time())
        else:
            last_played_time = record.last_played_time
        return GameTime(game_id, math.floor(total_seconds / 60.0), last_played_time)

    def _checkpoint_session(
        self,
        session: GameTimeSession,
        record: GameTimeRecord,
        now_monotonic: float,
        now_wall: float,
    ) -> None:
        elapsed = max(0.0, now_monotonic - session.checkpoint_monotonic)
        record.total_seconds += elapsed
        record.last_played_time = int(now_wall)
        session.session_seconds += elapsed
        session.checkpoint_monotonic = now_monotonic
        record.active_process = _active_process_from_session(
            session,
            now_monotonic,
            now_wall,
        )
        logger.info(
            "IndieGala Game-Time checkpoint: %s, total_minutes=%d",
            session.game_id,
            math.floor(record.total_seconds / 60.0),
        )

    def _finish_session(
        self,
        session: GameTimeSession,
        now_monotonic: float,
        now_wall: float,
    ) -> GameTime:
        record = self._records[session.game_id]
        elapsed = max(0.0, now_monotonic - session.checkpoint_monotonic)
        record.total_seconds += elapsed
        session.session_seconds += elapsed
        record.last_played_time = int(now_wall)
        record.active_process = None
        self._sessions.pop(session.game_id, None)
        minutes = math.floor(record.total_seconds / 60.0)
        self._last_reported_minutes[session.game_id] = minutes
        logger.info(
            "IndieGala Game-Time session ended: %s, session_seconds=%.1f, total_minutes=%d",
            session.game_id,
            session.session_seconds,
            minutes,
        )
        return GameTime(session.game_id, minutes, int(now_wall))


def _active_process_from_session(
    session: GameTimeSession,
    checkpoint_monotonic: float,
    checkpoint_wall_time: float,
) -> PersistedActiveProcess:
    return PersistedActiveProcess(
        pid=session.pid,
        create_time=session.create_time,
        checkpoint_monotonic=checkpoint_monotonic,
        checkpoint_wall_time=checkpoint_wall_time,
    )


def _same_runtime_process(session: GameTimeSession, process: ProcessInfo) -> bool:
    if session.pid != process.pid:
        return False
    if session.create_time is None or process.create_time is None:
        return True
    return abs(session.create_time - process.create_time) <= PROCESS_CREATE_TIME_TOLERANCE


def _can_resume_process(active: PersistedActiveProcess, process: ProcessInfo) -> bool:
    if active.pid != process.pid:
        return False
    if active.create_time is None or process.create_time is None:
        return False
    return abs(active.create_time - process.create_time) <= PROCESS_CREATE_TIME_TOLERANCE


def _decode_records(payload: Any) -> dict[str, GameTimeRecord]:
    if not isinstance(payload, dict) or payload.get("version") != GAME_TIME_CACHE_VERSION:
        raise ValueError("Unsupported Game-Time cache")
    games = payload.get("games")
    if not isinstance(games, dict):
        raise ValueError("Invalid Game-Time cache games")

    records: dict[str, GameTimeRecord] = {}
    for game_id, encoded in games.items():
        if not isinstance(game_id, str) or not game_id or not isinstance(encoded, dict):
            raise ValueError("Invalid Game-Time cache record")
        total_seconds = _nonnegative_number(encoded.get("total_seconds"))
        last_played_time = encoded.get("last_played_time")
        if last_played_time is not None and (
            isinstance(last_played_time, bool)
            or not isinstance(last_played_time, int)
            or last_played_time < 0
        ):
            raise ValueError("Invalid Game-Time last played timestamp")

        active_process = None
        active = encoded.get("active_process")
        if active is not None:
            if not isinstance(active, dict):
                raise ValueError("Invalid active Game-Time process")
            pid = active.get("pid")
            create_time = active.get("create_time")
            checkpoint_monotonic = _nonnegative_number(active.get("checkpoint_monotonic"))
            checkpoint_wall_time = _nonnegative_number(active.get("checkpoint_wall_time"))
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise ValueError("Invalid active Game-Time pid")
            if create_time is not None:
                create_time = _nonnegative_number(create_time)
            active_process = PersistedActiveProcess(
                pid=pid,
                create_time=create_time,
                checkpoint_monotonic=checkpoint_monotonic,
                checkpoint_wall_time=checkpoint_wall_time,
            )

        records[game_id] = GameTimeRecord(
            total_seconds=total_seconds,
            last_played_time=last_played_time,
            active_process=active_process,
        )
    return records


def _nonnegative_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected number")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise ValueError("Expected nonnegative finite number")
    return converted
