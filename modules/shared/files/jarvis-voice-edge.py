from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        raise FileNotFoundError(f"Env file not found: {path}")

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        env[key] = value

    return env


def voice_name_for_profile(profile: str) -> str | None:
    mapping = {
        "british-ai-assistant": "Daniel",
    }
    return mapping.get(profile)


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


async def say_text(profile: str, text: str) -> None:
    voice = voice_name_for_profile(profile)
    cmd = ["say"]
    if voice:
        cmd.extend(["-v", voice])
    cmd.append(text)
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


async def heartbeat(websocket, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        await websocket.send(json.dumps({"type": "ping", "timestamp": now_iso()}))


async def run_client(env: dict[str, str], state_file: Path) -> None:
    ws_url = env["JARVIS_VOICE_WS_URL"]
    wake_phrase = env["JARVIS_WAKE_PHRASE"]
    tts_mode = env["JARVIS_TTS_MODE"]
    voice_profile = env["JARVIS_TTS_VOICE_PROFILE"]
    reconnect_seconds = float(env.get("JARVIS_VOICE_RECONNECT_SECONDS", "5"))
    heartbeat_seconds = float(env.get("JARVIS_VOICE_HEARTBEAT_SECONDS", "20"))

    while True:
        try:
            write_state(
                state_file,
                {
                    "connected": False,
                    "status": "connecting",
                    "timestamp": now_iso(),
                    "ws_url": ws_url,
                    "wake_phrase": wake_phrase,
                    "tts_mode": tts_mode,
                    "voice_profile": voice_profile,
                },
            )

            async with websockets.connect(ws_url, ping_interval=None) as websocket:
                await websocket.send(
                    json.dumps(
                        {
                            "type": "voice_edge_hello",
                            "timestamp": now_iso(),
                            "wake_phrase": wake_phrase,
                            "tts_mode": tts_mode,
                            "voice_profile": voice_profile,
                            "platform": "darwin",
                        }
                    )
                )
                write_state(
                    state_file,
                    {
                        "connected": True,
                        "status": "connected",
                        "timestamp": now_iso(),
                        "ws_url": ws_url,
                        "wake_phrase": wake_phrase,
                        "tts_mode": tts_mode,
                        "voice_profile": voice_profile,
                    },
                )

                heartbeat_task = asyncio.create_task(heartbeat(websocket, heartbeat_seconds))
                try:
                    async for raw in websocket:
                        payload = json.loads(raw)
                        write_state(
                            state_file,
                            {
                                "connected": True,
                                "status": "connected",
                                "timestamp": now_iso(),
                                "last_message": payload,
                                "ws_url": ws_url,
                                "wake_phrase": wake_phrase,
                                "tts_mode": tts_mode,
                                "voice_profile": voice_profile,
                            },
                        )

                        if payload.get("type") == "speak_text" and payload.get("text"):
                            if tts_mode == "remote_text_local_tts":
                                await say_text(voice_profile, str(payload["text"]))
                finally:
                    heartbeat_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat_task
        except Exception as exc:  # pragma: no cover - surfaced via state/logs
            write_state(
                state_file,
                {
                    "connected": False,
                    "status": "reconnecting",
                    "timestamp": now_iso(),
                    "error": str(exc),
                    "ws_url": ws_url,
                    "wake_phrase": wake_phrase,
                    "tts_mode": tts_mode,
                    "voice_profile": voice_profile,
                },
            )
            await asyncio.sleep(reconnect_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Jarvis voice edge client")
    parser.add_argument("--env-file", required=True)
    args = parser.parse_args()

    env = dict(os.environ)
    env.update(load_env_file(Path(args.env_file)))
    state_file = Path(env.get("JARVIS_VOICE_EDGE_STATE_FILE", str(Path.home() / "Library/Application Support/jarvis/voice-edge-state.json")))
    asyncio.run(run_client(env, state_file))


if __name__ == "__main__":
    main()
