"""Daemon entrypoint: wires the agent, command service, local API and watchdog.

python pc-daemon/main.py --dry-run     # fakes for Edge/OS/LLM; safe on any machine
python pc-daemon/main.py               # real Edge + Windows control
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import secrets
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import uvicorn

import lifecycle
from agent.graph import AgentDeps, AgentRunner
from agent.memory import ConversationMemory
from bookmarks import BookmarkStore
from browser_prefs import BrowserPrefs
from build_info import allows_hobby_services, shows_update_notice
from cloud_relay_client import CloudRelayClient
from command_queue import CommandRequest
from config import Settings, get_settings, init_dpi_awareness, resource_root
from contracts import Bookmark, BrowserState, TimerInfo, VoiceState, new_id
from event_bus import EventBus
from executors.base import ActionResult, Browser, OSController, Screen
from executors.fakes import FakeBrowser, FakeOS, FakeScreen
from executors.subtitles import SubtitleController
from executors.video_fullscreener import VideoFullscreener
from executors.vision_grounding import VisionGrounder
from hud.qr import detect_lan_ip
from hud.viewmodel import HudViewModel
from lan_auth import LanAuth
from llm.base import TextLLM, VisionLLM
from llm.fake import DryRunRouter
from local_api import create_app
from room import RoomStore
from service import CommandService
from stt_engine import STTEngine, build_stt_engine
from telemetry import Telemetry
from timers import TimerService
from voice import VoicePrefsStore, VoiceService
from watchdog import EdgeWatchdog, make_edge_launcher, make_http_probe

log = logging.getLogger("couch-jarvis")
PWA_DIST = resource_root() / "web-remote" / "dist"


@dataclass
class Runtime:
    service: CommandService
    telemetry: Telemetry
    watchdog: EdgeWatchdog | None
    room_id: str
    room_store: RoomStore | None = None  # None in --dry-run (no identity on disk)
    bus: EventBus | None = None
    deps: AgentDeps | None = None
    stt: STTEngine | None = None
    cloud: CloudRelayClient | None = None
    tray: object | None = None


def build_llms(settings: Settings) -> tuple[TextLLM, VisionLLM]:
    """Text router and screen-reading model, preferred provider first, the other as fallback."""
    from hardware import text_model
    from llm.fallback import FallbackLLM
    from llm.gemini import GeminiClient
    from llm.ollama import OllamaClient

    local_text = OllamaClient(settings.ollama_url, text_model(settings.ollama_text_model))
    local_vlm = OllamaClient(settings.ollama_url, settings.ollama_vlm_model, timeout_s=90.0)
    if settings.gemini_api_key is None:
        return local_text, local_vlm
    cloud = GeminiClient(settings.gemini_api_key.get_secret_value())
    if (
        settings.router_provider == "ollama"
    ):  # local first, the cloud key only for the screen reader
        return local_text, FallbackLLM([cloud, local_vlm])
    return FallbackLLM([cloud, local_text]), FallbackLLM([cloud, local_vlm])


async def prepare_brain(settings: Settings) -> None:
    """Make the local model ready in the background and say plainly if it cannot be."""
    from brain import prepare_ollama
    from hardware import text_model

    try:
        status = await prepare_ollama(settings.ollama_url, text_model(settings.ollama_text_model))
    except Exception:  # a warm-up must never take the daemon down
        log.warning("brain warm-up failed", exc_info=True)
        return
    if status.usable:
        log.info("brain ready: %s", status.detail)
    elif settings.gemini_api_key is not None:
        log.info("local brain unavailable (%s); using the Gemini key", status.state)
    else:
        log.warning(
            "brain %s (%s): free-form commands will not work until you run Set up AI brain "
            "or add CJ_GEMINI_API_KEY",
            status.state,
            status.detail,
        )


def build_voice(settings: Settings, *, on_change: Callable[[VoiceState], None]) -> VoiceService:
    """Spoken replies: ElevenLabs if a key is set, else (source builds only) Edge neural voices,
    else Windows."""
    from voice.player import MciPlayer
    from voice.tts import (
        EdgeProvider,
        ElevenLabsProvider,
        TtsEngine,
        TtsProvider,
        UsageLedger,
        WindowsProvider,
    )

    providers: list[TtsProvider] = []
    if settings.elevenlabs_api_key is not None:
        ledger = UsageLedger(settings.voice_file.with_name("elevenlabs-usage.json"))
        providers.append(
            ElevenLabsProvider(
                settings.elevenlabs_api_key.get_secret_value(),
                ledger,
                monthly_chars=settings.elevenlabs_monthly_chars,
            )
        )
    if allows_hobby_services():  # edge-tts is not licensed for a product we sell
        providers.append(EdgeProvider())
    providers.append(WindowsProvider())
    engine = TtsEngine(providers, settings.voice_cache_dir)
    return VoiceService(
        VoicePrefsStore(settings.voice_file), engine, MciPlayer(), on_change=on_change
    )


def build_runtime(settings: Settings, *, dry_run: bool) -> Runtime:
    telemetry = Telemetry(None if dry_run else settings.telemetry_dir)
    bus = EventBus()
    watchdog: EdgeWatchdog | None = None
    stt: STTEngine | None = None
    room_store: RoomStore | None = None

    def browser_prefs_changed(state: BrowserState) -> None:
        service_ref[0].state.update(browser=state)

    prefs = BrowserPrefs(
        None if dry_run else settings.browser_file, on_change=browser_prefs_changed
    )

    def bookmarks_changed(items: list[Bookmark]) -> None:
        service_ref[0].state.update(bookmarks=items)

    bookmarks = BookmarkStore(
        None if dry_run else settings.bookmarks_file, on_change=bookmarks_changed
    )
    browser: Browser
    os_ctl: OSController
    screen: Screen
    if dry_run:
        fake_browser = FakeBrowser()
        browser, os_ctl, screen = fake_browser, FakeOS(fake_browser), FakeScreen()
        router: TextLLM = DryRunRouter()
        vlm: VisionLLM = DryRunRouter()
        room_id = "dry-run"
    else:
        from executors.edge_cdp import EdgeCDP
        from executors.os_controller import WindowsOS
        from executors.vision_grounding import MssScreen

        watchdog = EdgeWatchdog(
            make_http_probe(settings.cdp_url),
            make_edge_launcher(settings.edge_path, settings.edge_profile_dir, settings.cdp_port),
            telemetry=telemetry,
        )
        browser = EdgeCDP(settings.cdp_url, ensure_edge=watchdog.ensure_edge, prefs=prefs)
        os_ctl, screen = WindowsOS(), MssScreen()
        router, vlm = build_llms(settings)
        stt = build_stt_engine(settings, telemetry)
        room_store = RoomStore(settings.room_file)
        room_id = room_store.load_or_create().room_id

    grounder = VisionGrounder(vlm, screen, telemetry)
    fullscreener = VideoFullscreener(browser, grounder, os_ctl, telemetry)
    deps = AgentDeps(router, browser, os_ctl, fullscreener, grounder, telemetry)
    deps.prefs = prefs
    deps.remote_input = settings.remote_input
    deps.bookmarks = bookmarks
    deps.memory = ConversationMemory(None if dry_run else settings.memory_file)
    deps.subtitles = SubtitleController(browser, grounder, os_ctl)
    deps.subtitle_lang = settings.subtitle_lang.strip().lower() or "en"
    service_ref: list[CommandService] = []

    async def on_timer(info: TimerInfo) -> None:
        svc = service_ref[0]
        svc.progress(info.timer_id, "timer", "fired", info.label)
        action = {"pause": "panic", "sleep_display": "sleep_display"}.get(info.action)
        if action is not None:
            svc.submit(
                CommandRequest(
                    command_id=new_id(),
                    idempotency_key=f"timer-{info.timer_id}",
                    device_id="timer",
                    kind="button",
                    action=action,  # type: ignore[arg-type]
                )
            )

    def on_timers_changed() -> None:
        service_ref[0].state.update(timers=timers.list())

    timers = TimerService(on_timer, on_change=on_timers_changed)
    deps.timers = timers

    def on_voice_changed(voice: VoiceState) -> None:
        service_ref[0].state.update(voice=voice)

    if dry_run:  # no speakers: keep the saved choices and the settings flow, make no sound
        deps.voice = VoiceService(VoicePrefsStore(None), None, on_change=on_voice_changed)
    else:
        deps.voice = build_voice(settings, on_change=on_voice_changed)
    runner = AgentRunner(deps)
    service = CommandService(
        room_id=room_id,
        bus=bus,
        runner=runner,
        browser=browser,
        os_ctl=os_ctl,
        telemetry=telemetry,
        timers=timers,
        stt=stt,
        mode="lan" if dry_run else "cloud",
        remote_input=settings.remote_input,
    )
    service_ref.append(service)
    service.state.update(voice=deps.voice.state, browser=prefs.state, bookmarks=bookmarks.items())
    return Runtime(service, telemetry, watchdog, room_id, room_store, bus, deps, stt)


async def amain(args: argparse.Namespace) -> None:
    init_dpi_awareness()
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    rt = build_runtime(settings, dry_run=args.dry_run)
    if args.dry_run and rt.room_store is None:
        # --dry-run has no real identity, but pairing, LAN auth and the HUD QR all need one. Use a
        # throwaway store (or the file named by CJ_ROOM_FILE, so scripts can know the secret) and
        # a local room key, since there is no relay to hand one out.
        explicit = os.environ.get("CJ_ROOM_FILE")
        path = (
            Path(explicit)
            if explicit
            else Path(tempfile.mkdtemp(prefix="cj-dryrun-")) / "room.json"
        )
        rt.room_store = RoomStore(path)
        rt.room_store.load_or_create()
        if rt.room_store.identity.room_key is None:
            rt.room_store.set_room_key(secrets.token_hex(32))
    if args.rotate_secret and rt.room_store is not None:
        rt.room_store.rotate_secret()
        log.warning("room secret rotated: previously printed or shown QR codes no longer pair")

    host = "127.0.0.1" if args.dry_run else settings.lan_host
    port = args.port or settings.lan_port
    lan_auth = LanAuth(rt.room_store) if rt.room_store is not None else None
    if lan_auth is not None:
        rt.service.on_revoke = lan_auth.revoke
        rt.service.revoked |= set(rt.room_store.identity.revoked)  # type: ignore[union-attr]
    app = create_app(
        rt.service,
        rt.telemetry,
        token_verifier=lan_auth.verify_token if lan_auth else None,
        pairing=lan_auth.pair if lan_auth else None,
        allowed_origins=[
            settings.pwa_base_url,
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
        ],
        static_dir=PWA_DIST,
        delete_cloud=(lambda: delete_cloud_data(rt)) if rt.room_store is not None else None,
    )
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        ws_max_size=64 * 1024,  # largest legitimate frame is a ~25 KB audio chunk
        ssl_certfile=str(settings.lan_tls_cert) if settings.lan_tls_enabled else None,
        ssl_keyfile=str(settings.lan_tls_key) if settings.lan_tls_enabled else None,
    )
    server = uvicorn.Server(config)

    def stop_server() -> None:
        server.should_exit = True  # serve() returns, and the finally below shuts down cleanly

    lifecycle.set_stopper(stop_server)
    await rt.service.start()
    background: list[asyncio.Task[None]] = []
    if not args.dry_run:  # start/preload the local model now, not during your first command
        background.append(asyncio.create_task(prepare_brain(settings), name="brain-warm-up"))
    if settings.update_check and shows_update_notice() and not args.dry_run:
        service = rt.service

        def on_update(found: str | None) -> None:
            service.state.update(update_available=found)

        from update_check import run_update_check

        background.append(asyncio.create_task(run_update_check(on_update), name="update-check"))
    if rt.stt is not None:  # load the speech model now, not during your first voice command
        background.append(asyncio.create_task(rt.stt.warm_up(), name="stt-warm-up"))
    if args.hud and rt.room_store is not None:
        background.append(start_hud(rt, settings, host, port))
    if rt.watchdog is not None and not args.no_watchdog:
        background.append(asyncio.create_task(rt.watchdog.run(), name="edge-watchdog"))
    if rt.room_store is not None and rt.bus is not None and settings.relay_ws_url:
        service = rt.service

        def on_cloud_status(status: str) -> None:
            service.state.update(mode="cloud" if status == "up" else "lan")

        cloud = CloudRelayClient(
            url=settings.relay_ws_url,
            store=rt.room_store,
            service=service,
            bus=rt.bus,
            telemetry=rt.telemetry,
            on_status=on_cloud_status,
        )
        rt.cloud = cloud
        background.append(asyncio.create_task(cloud.run(), name="cloud-relay"))
    elif not args.dry_run:
        log.warning("CJ_RELAY_WS_URL is not set: running in LAN-only mode")
    if rt.room_store is not None and sys.stdout is not None and sys.stdout.isatty():
        # The URL embeds the room secret: show it on the console, never write it to log files.
        print("Pair a phone:", rt.room_store.identity.pairing_url(settings.pwa_base_url))
    log.info(
        "daemon up on http://%s:%d (room %s, dry_run=%s)", host, port, rt.room_id, args.dry_run
    )
    try:
        await server.serve()
    finally:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if rt.deps is not None and rt.deps.voice is not None:
            await rt.deps.voice.aclose()
        await rt.service.stop()


async def delete_cloud_data(rt: Runtime) -> str | None:
    """The relay forgets this room and its phones; then this PC starts over with a new room.

    Nothing local changes unless the relay confirmed (else the old room would linger there).
    Returns None when done, or why it was not done.
    """
    assert rt.room_store is not None
    if rt.cloud is not None:
        if rt.cloud.status != "up":
            return "The cloud is not reachable right now. Try again when the PC is online."
        if not await rt.cloud.delete_room():
            return "The cloud did not confirm. Nothing was deleted; try again later."
    rt.room_store.start_over()
    log.warning("cloud data deleted; restarting with a new room (phones must pair again)")
    # answer the request first, then restart with the new identity
    asyncio.get_running_loop().call_later(0.5, lifecycle.request_exit, lifecycle.EXIT_RESTART)
    return None


def start_hud(rt: Runtime, settings: Settings, host: str, port: int) -> asyncio.Task[None]:
    """Create the TV overlay (needs a running QApplication) and the task that feeds it."""
    from hud.controller import HudController
    from hud.tray import QrTray
    from hud.tv_overlay import TvOverlay

    store = rt.room_store
    assert store is not None

    def cloud_url() -> str | None:
        return store.identity.pairing_url(settings.pwa_base_url)

    def lan_url() -> str | None:
        ip = detect_lan_ip() if host in ("0.0.0.0", "") else host
        scheme = "https" if settings.lan_tls_enabled else "http"
        return store.identity.pairing_url(f"{scheme}://{ip}:{port}") if ip else None

    vm = HudViewModel(cloud_url=cloud_url, lan_url=lan_url, badge_hide_s=settings.hud_badge_hide_s)
    overlay = TvOverlay(vm, settings.hud_screen_index)
    overlay.show()

    def toggle_qr() -> bool:
        shown = vm.toggle_qr()
        overlay.refresh()
        return shown

    async def reset_qr() -> ActionResult:
        """New secret: old QR codes stop pairing (paired phones keep working)."""
        store.rotate_secret()
        if rt.cloud is not None:
            await rt.cloud.reconnect()  # tell the relay the new secret
        vm.show_qr()
        overlay.refresh()
        return ActionResult(True, "os")

    async def qr_action(action: str) -> ActionResult:
        if action == "hide":
            vm.hide_qr()
            overlay.refresh()
            return ActionResult(True, "os")
        if action == "reset":
            return await reset_qr()
        toggle_qr()
        return ActionResult(True, "os")

    rt.service.on_qr_toggle = toggle_qr
    rt.service.on_activity = vm.touch
    if rt.deps is not None:
        rt.deps.qr_action = qr_action

        def show_qr() -> None:
            vm.show_qr()
            overlay.refresh()

        rt.deps.show_qr = show_qr
    tray = QrTray(toggle_qr, reset_qr, lambda: lifecycle.request_exit(lifecycle.EXIT_QUIT))
    rt.tray = tray  # keep the tray icon alive for the life of the app
    controller = HudController(rt.service, vm, on_change=overlay.refresh)
    return asyncio.create_task(controller.run(), name="hud")


def run_with_qt(args: argparse.Namespace) -> None:
    """Run the daemon on a Qt-integrated event loop so the overlay and asyncio share a thread."""
    import qasync
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(amain(args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CouchJarvis PC daemon")
    parser.add_argument(
        "--dry-run", action="store_true", help="use fakes instead of Edge/Windows/LLMs"
    )
    parser.add_argument("--no-watchdog", action="store_true", help="do not supervise Edge")
    parser.add_argument("--port", type=int, default=0, help="override the LAN/SSE port")
    parser.add_argument(
        "--hud", action="store_true", help="show the TV overlay (QR, toasts, timers)"
    )
    parser.add_argument(
        "--rotate-secret",
        action="store_true",
        help="issue a new QR secret (invalidates old QR codes; paired phones keep working)",
    )
    args = parser.parse_args(argv)
    with contextlib.suppress(KeyboardInterrupt):
        if args.hud:
            run_with_qt(args)
        else:
            asyncio.run(amain(args))
    return lifecycle.exit_code()  # EXIT_QUIT / EXIT_RESTART tell the supervisor what to do


if __name__ == "__main__":
    sys.exit(main())
