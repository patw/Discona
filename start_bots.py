import subprocess
import sys
import os
import signal
import time
from dotenv import load_dotenv
from moofile import Collection

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(SCRIPT_DIR, 'discord_llama.py')

MAX_CONSECUTIVE_RESTARTS = 5   # crashes within the window before giving up
RESTART_WINDOW = 60            # seconds
BASE_BACKOFF = 3               # seconds before the first restart

bot_procs = []


def log(msg):
    print(f"[discona] {msg}", flush=True)


def signal_handler(sig, frame):
    log("Shutting down bots...")
    for bp in bot_procs:
        if bp.alive and bp.proc is not None:
            bp.proc.terminate()
    time.sleep(2)
    for bp in bot_procs:
        if bp.alive and bp.proc is not None and bp.proc.poll() is None:
            bp.proc.kill()
    sys.exit(0)


class BotProcess:
    def __init__(self, bot):
        self.bot = bot
        self.proc = None
        self.alive = False
        self.restarts = []       # timestamps of recent crashes
        self.next_start = 0.0    # earliest time we may relaunch
        self.gave_up = False

    @property
    def name(self):
        return self.bot.get('name', self.bot['_id'])

    def launch(self):
        self.proc = subprocess.Popen(
            [sys.executable, RUNNER, self.bot['_id']],
            cwd=SCRIPT_DIR,
        )
        self.alive = True
        log(f"Started bot: {self.name} (PID {self.proc.pid})")

    def handle_exit(self, returncode):
        self.alive = False
        now = time.time()
        self.restarts = [t for t in self.restarts if now - t < RESTART_WINDOW]
        log(f"Bot {self.name} exited with code {returncode}.")
        if len(self.restarts) >= MAX_CONSECUTIVE_RESTARTS:
            self.gave_up = True
            log(f"  -> crashed too many times in {RESTART_WINDOW}s, giving up on {self.name}")
            return
        delay = BASE_BACKOFF * (len(self.restarts) + 1)
        self.next_start = now + delay
        self.restarts.append(now)
        log(f"  -> will restart in {delay}s")


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    data_dir = os.getenv('DATA_DIR', '.')
    log(f"Loading bots from {data_dir}...")

    try:
        bots_col = Collection(os.path.join(data_dir, 'bots.bson'), indexes=['name'])
        bots = bots_col.find({}).to_list()
    except Exception as e:
        log(f"An error occurred while reading bots: {e}")
        return

    enabled = [b for b in bots if b.get('enabled', True) is not False]
    if len(enabled) != len(bots):
        log(f"Skipping {len(bots) - len(enabled)} disabled bot(s).")

    if not enabled:
        log("No enabled bots found.")
        return

    log(f"Found {len(enabled)} enabled bot(s).")

    global bot_procs
    bot_procs = [BotProcess(b) for b in enabled]
    for bp in bot_procs:
        bp.launch()

    try:
        while True:
            time.sleep(1)

            # Reap exited processes and decide whether to restart them.
            for bp in bot_procs:
                if bp.alive and bp.proc is not None and bp.proc.poll() is not None:
                    bp.handle_exit(bp.proc.returncode)

            # Launch anything whose backoff has elapsed.
            now = time.time()
            for bp in bot_procs:
                if not bp.alive and not bp.gave_up and bp.next_start and now >= bp.next_start:
                    bp.launch()

            # Exit once every bot has permanently given up.
            if all(bp.gave_up for bp in bot_procs):
                log("All bots have stopped. Exiting manager.")
                return
    except Exception as e:
        log(f"An error occurred: {e}")
        signal_handler(None, None)


if __name__ == "__main__":
    main()
