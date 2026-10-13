import threading
import time
import os
import logging
import psutil
import pytz
from concurrent.futures import Future
from datetime import datetime, timedelta
from plugins.plugin_registry import get_plugin_instance
from utils.image_utils import compute_image_hash
from utils.time_utils import clock_interval_elapsed, seconds_until_next_interval
from model import RefreshInfo, DisplayHold
from refresh_schedule import (PRERENDER_ALWAYS, PRERENDER_JUST_IN_TIME, PRERENDER_ON_SCREEN, hold_expired,
                              hold_wake_times, new_hold, next_plugin_refresh, next_window_boundary, prerender_lead)
from buttons import (ACTION_NEXT_PLAYLIST, ACTION_NEXT_PLUGIN, ACTION_NONE, ACTION_PIN, ACTION_PREVIOUS_PLAYLIST,
                     ACTION_PREVIOUS_PLUGIN, ACTION_REFRESH, ACTION_SCREEN_OFF, PLAYLIST_ACTION_PREFIX,
                     PLUGIN_ACTION_PREFIX, get_button_config)
from PIL import Image

logger = logging.getLogger(__name__)

# The worker never sleeps longer than this, so it notices clock jumps and config edits.
MAX_SLEEP_SECONDS = 60
# Scheduled work waits this long at most for the clock to sync after boot (there's no RTC).
CLOCK_SYNC_TIMEOUT_SECONDS = 180
TIMESYNC_DIR = "/run/systemd/timesync"
MANUAL_IMAGE_FILE = "manual_update.png"
# Buttons accept presses again after this long, even if a stuck render never finishes.
BUTTON_BUSY_TIMEOUT_SECONDS = 300


class DisplayRequest:
    """A pending request to put the current target on screen. Later requests merge into it."""

    def __init__(self):
        self.advance = False
        self.force = False
        self.futures = []


class Target:
    """What belongs on screen: a playlist (which cycles), a held plugin instance, or a manual image."""

    def __init__(self, key, playlist=None, instance=None, plugin_id=None):
        self.key = key
        self.playlist = playlist
        self.instance = instance
        self.plugin_id = plugin_id

    @property
    def cycles(self):
        return self.playlist is not None and self.instance is None

    @property
    def manual(self):
        return self.playlist is None


class RefreshTask:
    """Schedules, renders, and displays plugin images on a single background thread.

    Scheduled work (playlist cycles, window changes, hold expiry, pre-renders) and user requests (web UI and
    buttons) go into one queue. User requests run first, then the on-screen playlist, then pre-renders.
    Rendering is separate from displaying: only the target that owns the screen is displayed, and a
    playlist's rotation only advances when one of its images is displayed.
    """

    def __init__(self, device_config, display_manager, led=None):
        self.device_config = device_config
        self.display_manager = display_manager
        self.led = led

        self.thread = None
        # Shares the config lock, so queue state and model objects change together.
        self.condition = threading.Condition(device_config.lock)
        self.running = False

        self._jobs = []          # (callable, future) user requests, run in order
        self._display = None     # pending DisplayRequest
        self._prerenders = {}    # playlist id -> True

        self._started_monotonic = time.monotonic()
        self._clock_synced = False
        self._owner_key = None   # key of the target last put on screen
        self._displayed = None   # (playlist id, plugin id, instance name) of the last displayed instance
        self._last_tick = None   # when the on-screen playlist last moved on
        self._prerender_ticks = {}
        self._jit_boundary = None
        self._retry_after = None  # when to retry a single-plugin playlist whose render failed
        self._button_future = None  # completes when the last button press's action is on screen
        self._button_pressed_at = 0
        self._working = False    # whether a job or display is running, for the busy LED

    def start(self):
        """Starts the background thread for refreshing the display."""
        if not self.thread or not self.thread.is_alive():
            logger.info("Starting refresh task")
            with self.condition:
                self._restore_state()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.running = True
            self.thread.start()

    def stop(self):
        """Stops the refresh task by notifying the background thread to exit."""
        with self.condition:
            self.running = False
            self.condition.notify_all()  # Wake the thread to let it exit
        if self.thread:
            logger.info("Stopping refresh task")
            self.thread.join()

    def signal_config_change(self):
        """Wakes the background thread to re-plan after a config change."""
        with self.condition:
            self.condition.notify_all()

    # ----- Requests from the web UI and buttons -----

    def show_instance(self, playlist, plugin_instance):
        """Renders and displays a plugin instance, holding it on screen. Returns a Future."""
        with self.condition:
            self._wake_screen()
            self._set_hold(DisplayHold.INSTANCE, playlist_id=playlist.id, plugin_id=plugin_instance.plugin_id,
                           instance_name=plugin_instance.name)
            return self._queue_display(force=True)

    def show_manual(self, plugin_id, plugin_settings):
        """Renders a plugin with ad hoc settings and holds the result on screen. Returns a Future."""
        with self.condition:
            self._wake_screen()
            return self._queue_job(lambda: self._run_manual(plugin_id, plugin_settings))

    def show_image(self, image):
        """Displays an image without changing the schedule, e.g. the startup image. Returns a Future."""
        with self.condition:
            return self._queue_job(lambda: self.display_manager.display_image(image))

    def resume_schedule(self):
        """Ends any hold so the playlist schedule decides what's on screen."""
        with self.condition:
            if self.device_config.display_hold:
                logger.info("Resuming the playlist schedule.")
                self.device_config.display_hold = None
                self.device_config.write_config()
            self.condition.notify_all()

    def handle_button(self, button, long_press=False):
        """Runs the action configured for a button press. Returns a Future, or None if nothing was queued.

        Presses are ignored until the previous press's action has finished, since the screen takes a while to
        show it and a second press would otherwise change what the first one does.
        """
        with self.condition:
            if self.led:
                self.led.clear_error()
                if long_press:
                    self.led.flash()

            busy = self._button_future and not self._button_future.done()
            if busy and time.monotonic() - self._button_pressed_at < BUTTON_BUSY_TIMEOUT_SECONDS:
                logger.info(f"Button {button} ignored, still working on the previous press.")
                return None

            if self.device_config.screen_off:
                logger.info(f"Button {button} pressed while the screen is off, waking it.")
                future = self._wake_screen()
            else:
                press = "long" if long_press else "short"
                action = get_button_config(self.device_config)[button][press] or ACTION_NONE
                if long_press and action == ACTION_NONE:
                    _, instance = self._on_screen_instance(self._now())
                    if instance:
                        action = instance.button_actions.get(button) or ACTION_NONE
                logger.info(f"Button {button} {press} press: {action}")
                future = self.perform_action(action)

            self._button_future = future
            self._button_pressed_at = time.monotonic()
            return future

    def perform_action(self, action):
        """Runs a button action. Returns a Future, or None if nothing was queued."""
        with self.condition:
            now = self._now()
            if action == ACTION_NONE:
                return None

            if action == ACTION_SCREEN_OFF:
                return self._queue_job(self._turn_screen_off)

            if action in (ACTION_NEXT_PLAYLIST, ACTION_PREVIOUS_PLAYLIST) or action.startswith(PLAYLIST_ACTION_PREFIX):
                playlist = self._choose_playlist(action, now)
                if not playlist:
                    logger.info(f"No playlist with plugins for '{action}'.")
                    return None
                self._set_hold(DisplayHold.PLAYLIST, playlist_id=playlist.id)
                return self._queue_display(advance=True)

            if action == ACTION_PIN and self.device_config.display_hold and self.device_config.display_hold.pinned:
                logger.info("Unpinning, back to the playlist schedule.")
                self.device_config.display_hold = None
                self.device_config.write_config()
                self.condition.notify_all()
                return None

            playlist, instance = self._on_screen_instance(now)
            if action in (ACTION_NEXT_PLUGIN, ACTION_PREVIOUS_PLUGIN):
                if not playlist or not playlist.plugins:
                    return None
                step = 1 if action == ACTION_NEXT_PLUGIN else -1
                if instance in playlist.plugins:
                    instance = playlist.plugins[(playlist.plugins.index(instance) + step) % len(playlist.plugins)]
                else:
                    instance = playlist.peek_next_plugin(step)
            if not instance:
                logger.info(f"No plugin instance on screen for '{action}'.")
                return None

            target = dict(playlist_id=playlist.id, plugin_id=instance.plugin_id, instance_name=instance.name)
            if action.startswith(PLUGIN_ACTION_PREFIX):
                action_id = action[len(PLUGIN_ACTION_PREFIX):]
                return self._queue_job(lambda: self._run_plugin_action(playlist, instance, action_id))
            if action == ACTION_PIN:
                self._set_hold(DisplayHold.INSTANCE, pinned=True, **target)
                return self._queue_display()
            if action == ACTION_REFRESH:
                self._set_hold(DisplayHold.INSTANCE, **target)
                return self._queue_display(force=True)
            if action in (ACTION_NEXT_PLUGIN, ACTION_PREVIOUS_PLUGIN):
                self._set_hold(DisplayHold.INSTANCE, **target)
                return self._queue_display()

            logger.warning(f"Unknown button action '{action}'.")
            return None

    def get_status(self):
        """Describes the hold and screen state for the dashboard."""
        with self.condition:
            now = self._now()
            playlist_manager = self.device_config.get_playlist_manager()
            scheduled = playlist_manager.determine_active_playlist(now)
            status = {
                "screen_off": self.device_config.screen_off,
                "scheduled_playlist": scheduled.name if scheduled else None,
                "hold": None,
            }
            hold = self.device_config.display_hold
            if hold:
                playlist = playlist_manager.get_playlist_by_id(hold.playlist_id)
                if hold.kind == DisplayHold.PLAYLIST:
                    label = f"playlist {playlist.name}" if playlist else "a playlist"
                elif hold.kind == DisplayHold.INSTANCE:
                    label = hold.instance_name
                else:
                    label = "a manual update"
                ends_at = hold.get_ends_at_dt()
                if ends_at:
                    ends_at = ends_at.astimezone(now.tzinfo)
                    if self.device_config.get_config("time_format") == "24h":
                        ends_at = ends_at.strftime("%H:%M")
                    else:
                        ends_at = ends_at.strftime("%I:%M %p").lstrip("0")
                status["hold"] = {
                    "label": label,
                    "pinned": hold.pinned,
                    "until_schedule_change": bool(hold.min_until),
                    "ends_at": ends_at,
                }
            return status

    # ----- Queue -----

    def _queue_job(self, job):
        future = Future()
        self._jobs.append((job, future))
        self._update_busy_led()
        self.condition.notify_all()
        return future

    def _queue_display(self, advance=False, force=False):
        if self._display is None:
            self._display = DisplayRequest()
        self._display.advance |= advance
        self._display.force |= force
        future = Future()
        self._display.futures.append(future)
        self._update_busy_led()
        self.condition.notify_all()
        return future

    def _update_busy_led(self):
        """The busy LED covers queued and running jobs and displays, but not background pre-renders."""
        if self.led:
            self.led.set_busy(bool(self._jobs or self._display or self._working))

    def _pop_work(self):
        if self._jobs:
            job, future = self._jobs.pop(0)
            return ("job", job, [future])
        if self._display:
            request, self._display = self._display, None
            return ("display", request, request.futures)
        if self._prerenders:
            playlist_id = next(iter(self._prerenders))
            del self._prerenders[playlist_id]
            return ("prerender", playlist_id, [])
        return None

    def _run(self):
        """Plans scheduled work, then runs queued work until `stop()` is called."""
        while True:
            with self.condition:
                while True:
                    if not self.running:
                        return
                    try:
                        wait_seconds = self._plan(self._now())
                    except Exception:
                        logger.exception("Error planning scheduled work")
                        wait_seconds = MAX_SLEEP_SECONDS
                    work = self._pop_work()
                    if work:
                        break
                    if self.led and self._clock_synced:
                        # Nothing needs drawing after startup, e.g. the image from before a restart is current.
                        self.led.set_starting(False)
                    self.condition.wait(timeout=wait_seconds)
            self._execute(work)

    def _execute(self, work):
        kind, payload, futures = work
        visible = kind != "prerender"
        if visible:
            with self.condition:
                self._working = True
                self._update_busy_led()
        try:
            if kind == "job":
                result = payload()
            elif kind == "display":
                result = self._run_display(payload)
            else:
                result = self._run_prerender(payload)
            for future in futures:
                future.set_result(result)
        except Exception as e:
            logger.exception(f"Error during {kind}")
            if visible and self.led:
                self.led.error()
            for future in futures:
                future.set_exception(e)
        finally:
            if visible:
                with self.condition:
                    self._working = False
                    self._update_busy_led()
                if self.led:
                    self.led.set_starting(False)

    # ----- Scheduling -----

    def _plan(self, now):
        """Queues scheduled work that's due and returns the seconds until the next scheduled event."""
        playlist_manager = self.device_config.get_playlist_manager()
        scheduled = playlist_manager.determine_active_playlist(now)
        playlist_manager.active_playlist = scheduled.name if scheduled else None
        scheduled_id = scheduled.id if scheduled else None

        hold = self.device_config.display_hold
        if hold and hold_expired(hold, now, scheduled_id):
            logger.info("Hold ended, back to the playlist schedule.")
            self.device_config.display_hold = None
            self.device_config.write_config()

        if not self._clock_ready():
            return 5

        # A backwards clock jump would otherwise delay the next cycle until the clock catches up.
        if self._last_tick and self._last_tick > now + timedelta(minutes=1):
            self._last_tick = None

        target = self._target(scheduled)
        key = target.key if target else None
        if key != self._owner_key:
            if target:
                self._queue_display(advance=True)
            else:
                self._owner_key = None
            self._last_tick = now
        elif target and target.cycles and self._cycle_due(target.playlist, now):
            logger.info(f"Cycle due for playlist '{target.playlist.name}'.")
            self._queue_display(advance=True)
            self._last_tick = now

        wake_times = [now + timedelta(seconds=MAX_SLEEP_SECONDS)]
        if target and target.cycles:
            wake_times.extend(self._next_cycle_times(target.playlist, now))
        boundary = next_window_boundary(playlist_manager.playlists, now)
        if boundary:
            wake_times.append(boundary + timedelta(seconds=1))
        if self.device_config.display_hold:
            wake_times.extend(t + timedelta(seconds=1) for t in hold_wake_times(self.device_config.display_hold))
        wake_times.extend(self._plan_prerenders(now, target, boundary))

        return max(1.0, min((t - now).total_seconds() for t in wake_times))

    def _cycle_due(self, playlist, now):
        """Whether the playlist on screen should move to its next plugin now.

        A playlist with a single plugin has nothing to rotate to, so it's only redrawn when that plugin is due
        for new content. After a failed render it waits a cycle before trying again.
        """
        if len(playlist.plugins) > 1:
            return clock_interval_elapsed(self._last_tick, now, playlist.cycle_interval_seconds)
        retry_ready = self._retry_after is None or now >= self._retry_after
        return retry_ready and playlist.plugins[0].should_refresh(now)

    def _next_cycle_times(self, playlist, now):
        """When the playlist on screen could next be due, for _cycle_due."""
        if len(playlist.plugins) > 1:
            return [now + timedelta(seconds=seconds_until_next_interval(now, playlist.cycle_interval_seconds) + 1)]
        times = [next_plugin_refresh(playlist.plugins[0], now), self._retry_after]
        return [t + timedelta(seconds=1) for t in times if t and t > now]

    def _plan_prerenders(self, now, target, boundary):
        """Queues pre-renders for playlists that aren't on screen and returns when to check again."""
        mode = self.device_config.get_config("prerender_mode", default=PRERENDER_ON_SCREEN)
        playlist_manager = self.device_config.get_playlist_manager()
        on_screen_id = target.playlist.id if target and target.playlist else None
        wake_times = []

        if mode == PRERENDER_ALWAYS:
            for playlist in playlist_manager.playlists:
                if not playlist.plugins or playlist.id == on_screen_id:
                    continue
                last = self._prerender_ticks.setdefault(playlist.id, now)
                # A single plugin's image only changes when the plugin is due, so there's nothing to render before.
                single_not_due = len(playlist.plugins) == 1 and not playlist.plugins[0].should_refresh(now)
                if clock_interval_elapsed(last, now, playlist.cycle_interval_seconds) and not single_not_due:
                    self._prerenders[playlist.id] = True
                    self._prerender_ticks[playlist.id] = now
                wake_times.append(now + timedelta(
                    seconds=seconds_until_next_interval(now, playlist.cycle_interval_seconds) + 1))

        elif mode == PRERENDER_JUST_IN_TIME and boundary and boundary != self._jit_boundary:
            upcoming = playlist_manager.determine_active_playlist(boundary)
            if upcoming and upcoming.id != on_screen_id:
                start_at = boundary - prerender_lead(upcoming.peek_next_plugin())
                if now >= start_at:
                    self._prerenders[upcoming.id] = True
                    self._jit_boundary = boundary
                else:
                    wake_times.append(start_at)

        return wake_times

    def _clock_ready(self):
        """Whether the clock has synced since boot, or the wait for it has timed out."""
        if not self._clock_synced:
            timed_out = time.monotonic() - self._started_monotonic >= CLOCK_SYNC_TIMEOUT_SECONDS
            synced = not os.path.isdir(TIMESYNC_DIR) or os.path.exists(os.path.join(TIMESYNC_DIR, "synchronized"))
            if synced or timed_out:
                if not synced:
                    logger.warning("Clock hasn't synced, running the schedule anyway.")
                self._clock_synced = True
        return self._clock_synced

    def _restore_state(self):
        """Picks up what was on screen before a restart, so it isn't redrawn needlessly."""
        refresh_info = self.device_config.get_refresh_info()
        playlist = self.device_config.get_playlist_manager().get_playlist(refresh_info.playlist)
        if playlist and refresh_info.plugin_instance:
            self._displayed = (playlist.id, refresh_info.plugin_id, refresh_info.plugin_instance)
        self._last_tick = refresh_info.get_refresh_datetime()

        hold = self.device_config.display_hold
        if hold:
            target = self._target(None)
            self._owner_key = target.key if target else None
        elif playlist and refresh_info.refresh_type == "Playlist":
            self._owner_key = ("playlist", playlist.id)

    def _target(self, scheduled):
        """Returns the Target that owns the screen: the hold if there is one, otherwise the scheduled playlist."""
        hold = self.device_config.display_hold
        playlist_manager = self.device_config.get_playlist_manager()
        if hold:
            if hold.kind == DisplayHold.MANUAL:
                return Target(("manual", hold.started_at), plugin_id=hold.plugin_id)
            playlist = playlist_manager.get_playlist_by_id(hold.playlist_id)
            if playlist and hold.kind == DisplayHold.INSTANCE:
                instance = playlist.find_plugin(hold.plugin_id, hold.instance_name)
                if instance:
                    return Target(("instance", playlist.id, instance.plugin_id, instance.name), playlist, instance)
            elif playlist and playlist.plugins:
                return Target(("playlist", playlist.id), playlist)
            logger.info("The held playlist or plugin instance no longer exists, ending the hold.")
            self.device_config.display_hold = None
            self.device_config.write_config()
        if scheduled:
            return Target(("playlist", scheduled.id), scheduled)
        return None

    def _set_hold(self, kind, pinned=False, **target):
        now = self._now()
        scheduled = self.device_config.get_playlist_manager().determine_active_playlist(now)
        self.device_config.display_hold = new_hold(kind, self.device_config.get_config("hold_settings", default=None),
                                                   now, scheduled.id if scheduled else None, pinned=pinned, **target)
        self.device_config.write_config()

    def _wake_screen(self):
        if not self.device_config.screen_off:
            return None
        self.device_config.screen_off = False
        self.device_config.write_config()
        return self._queue_display()

    def _on_screen_instance(self, now):
        """Returns the (playlist, plugin instance) on screen or about to be, either of which may be None."""
        playlist_manager = self.device_config.get_playlist_manager()
        hold = self.device_config.display_hold
        if hold and hold.kind in (DisplayHold.INSTANCE, DisplayHold.PLAYLIST):
            playlist = playlist_manager.get_playlist_by_id(hold.playlist_id)
            if playlist and hold.kind == DisplayHold.INSTANCE:
                return playlist, playlist.find_plugin(hold.plugin_id, hold.instance_name)
            if playlist:
                return playlist, playlist.current_plugin() or playlist.peek_next_plugin()
        if self._displayed:
            playlist = playlist_manager.get_playlist_by_id(self._displayed[0])
            instance = playlist.find_plugin(*self._displayed[1:]) if playlist else None
            if instance:
                return playlist, instance
        scheduled = playlist_manager.determine_active_playlist(now)
        if not scheduled:
            return None, None
        return scheduled, scheduled.current_plugin() or scheduled.peek_next_plugin()

    def _choose_playlist(self, action, now):
        """Returns the playlist a playlist action switches to, following the playlist order."""
        playlist_manager = self.device_config.get_playlist_manager()
        if action.startswith(PLAYLIST_ACTION_PREFIX):
            playlist = playlist_manager.get_playlist_by_id(action[len(PLAYLIST_ACTION_PREFIX):])
            return playlist if playlist and playlist.plugins else None

        candidates = [p for p in playlist_manager.playlists if p.plugins]
        if not candidates:
            return None
        step = 1 if action == ACTION_NEXT_PLAYLIST else -1
        current, _ = self._on_screen_instance(now)
        if current in candidates:
            return candidates[(candidates.index(current) + step) % len(candidates)]
        return candidates[0] if step > 0 else candidates[-1]

    # ----- Work -----

    def _run_display(self, request):
        """Puts the current target on screen, rendering first if its image is out of date."""
        if self.device_config.get_config("log_system_stats"):
            self.log_system_stats()

        now = self._now()
        with self.condition:
            scheduled = self.device_config.get_playlist_manager().determine_active_playlist(now)
            target = self._target(scheduled)
            self._owner_key = target.key if target else None
            if not target:
                logger.info("No active playlist with plugins, nothing to display.")
                return None

            playlist, instance = target.playlist, target.instance
            if playlist and instance is None:
                instance = (playlist.peek_next_plugin() if request.advance
                            else playlist.current_plugin() or playlist.peek_next_plugin())

        if target.manual:
            return self._show_saved_manual(target, now)

        logger.info(f"Displaying '{instance.name}' from playlist '{playlist.name}'.")
        try:
            image = self._render(instance, now, force=request.force)
        except Exception:
            if target.cycles and request.advance:
                with self.condition:
                    if len(playlist.plugins) > 1:
                        # Move past a failing plugin so it doesn't block the rest of the playlist.
                        playlist.advance_to(instance)
                        self.device_config.write_config()
                    else:
                        self._retry_after = now + timedelta(seconds=playlist.cycle_interval_seconds)
            raise
        self._retry_after = None
        self._show(image, now, instance.plugin_id, playlist, instance)

    def _run_prerender(self, playlist_id):
        now = self._now()
        with self.condition:
            playlist = self.device_config.get_playlist_manager().get_playlist_by_id(playlist_id)
            instance = playlist.peek_next_plugin() if playlist else None
        if instance:
            logger.info(f"Pre-rendering '{instance.name}' from playlist '{playlist.name}'.")
            self._render(instance, now, force=False)

    def _run_manual(self, plugin_id, plugin_settings):
        now = self._now()
        image = self._get_plugin(plugin_id).generate_image(plugin_settings, self.device_config)
        image.save(os.path.join(self.device_config.plugin_image_dir, MANUAL_IMAGE_FILE))
        with self.condition:
            self._set_hold(DisplayHold.MANUAL, plugin_id=plugin_id)
            self._owner_key = self._target(None).key
        self._show(image, now, plugin_id)

    def _run_plugin_action(self, playlist, instance, action_id):
        now = self._now()
        logger.info(f"Running action '{action_id}' for '{instance.name}'.")
        image = self._get_plugin(instance.plugin_id).run_button_action(action_id, instance.settings, self.device_config)
        image.save(os.path.join(self.device_config.plugin_image_dir, instance.get_image_path()))
        with self.condition:
            # Counts as a refresh, so the next display doesn't replace the action's image.
            instance.latest_refresh_time = now.isoformat()
            self._set_hold(DisplayHold.INSTANCE, playlist_id=playlist.id, plugin_id=instance.plugin_id,
                           instance_name=instance.name)
            self._owner_key = self._target(None).key
        self._show(image, now, instance.plugin_id, playlist, instance)

    def _turn_screen_off(self):
        with self.condition:
            logger.info("Turning the screen off.")
            self.device_config.screen_off = True
            # Forget the displayed image so waking up redraws it.
            self.device_config.refresh_info.image_hash = None
            self.device_config.write_config()
        self.display_manager.display_image(Image.new("RGB", self.device_config.get_resolution(), "white"))

    def _show_saved_manual(self, target, now):
        path = os.path.join(self.device_config.plugin_image_dir, MANUAL_IMAGE_FILE)
        if not os.path.exists(path):
            logger.warning("The manual update image is missing, ending the hold.")
            self.resume_schedule()
            return None
        with Image.open(path) as img:
            image = img.copy()
        self._show(image, now, target.plugin_id)

    def _render(self, instance, now, force):
        """Returns the instance's image, rendering it if forced, due, or missing."""
        path = os.path.join(self.device_config.plugin_image_dir, instance.get_image_path())
        if not (force or instance.should_refresh(now) or not os.path.exists(path)):
            logger.info(f"Using the saved image for '{instance.name}'.")
            with Image.open(path) as img:
                return img.copy()

        logger.info(f"Rendering plugin instance '{instance.name}'.")
        plugin = self._get_plugin(instance.plugin_id)
        start = time.monotonic()
        image = plugin.generate_image(instance.settings, self.device_config)
        image.save(path)
        with self.condition:
            instance.latest_refresh_time = now.isoformat()
            instance.last_render_seconds = round(time.monotonic() - start, 1)
            self.device_config.write_config()
        return image

    def _show(self, image, now, plugin_id, playlist=None, instance=None):
        """Displays the image unless the screen is off or it's already shown, then records it."""
        image_hash = compute_image_hash(image)
        with self.condition:
            screen_off = self.device_config.screen_off
            latest_hash = self.device_config.get_refresh_info().image_hash
            plugin_config = self.device_config.get_plugin(plugin_id) or {}

        if screen_off:
            logger.info("Screen is off, not displaying.")
            return
        if image_hash == latest_hash:
            logger.info("Image already displayed, skipping refresh.")
        else:
            self.display_manager.display_image(image, image_settings=plugin_config.get("image_settings", []))

        with self.condition:
            if playlist and instance:
                playlist.advance_to(instance)
                self._displayed = (playlist.id, instance.plugin_id, instance.name)
                refresh_info = RefreshInfo("Playlist", plugin_id, now.isoformat(), image_hash,
                                           playlist=playlist.name, plugin_instance=instance.name)
            else:
                self._displayed = None
                refresh_info = RefreshInfo("Manual Update", plugin_id, now.isoformat(), image_hash)
            logger.info(f"Displayed. | refresh_info: {refresh_info.to_dict()}")
            self.device_config.refresh_info = refresh_info
            self.device_config.write_config()

    def _get_plugin(self, plugin_id):
        plugin_config = self.device_config.get_plugin(plugin_id)
        if plugin_config is None:
            raise RuntimeError(f"Plugin '{plugin_id}' is not installed.")
        return get_plugin_instance(plugin_config)

    def _now(self):
        """Retrieves the current datetime based on the device's configured timezone."""
        tz_str = self.device_config.get_config("timezone", default="UTC")
        return datetime.now(pytz.timezone(tz_str))

    def log_system_stats(self):
        metrics = {
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_percent': psutil.disk_usage('/').percent,
            'load_avg_1_5_15': os.getloadavg(),
            'swap_percent': psutil.swap_memory().percent,
            'net_io': {
                'bytes_sent': psutil.net_io_counters().bytes_sent,
                'bytes_recv': psutil.net_io_counters().bytes_recv
            }
        }

        logger.info(f"System Stats: {metrics}")
