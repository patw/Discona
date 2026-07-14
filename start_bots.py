import subprocess
import sys
import os
import signal
import time
from dotenv import load_dotenv
from moofile import Collection

load_dotenv()

processes = []


def signal_handler(sig, frame):
    print("\nShutting down bots...")
    for p in processes:
        p.terminate()
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    data_dir = os.getenv('DATA_DIR', '.')
    print(f"Loading bots from {data_dir}...")

    try:
        bots_col = Collection(os.path.join(data_dir, 'bots.bson'), indexes=['name'], readonly=True)
        bots = bots_col.find({}).to_list()
        bots_col.close()  # Release the collection — we only needed the bot list

        if not bots:
            print("No bots found.")
            return

        print(f"Found {len(bots)} configured bots.")

        for bot in bots:
            bot_id = bot['_id']
            bot_name = bot['name']
            print(f"Starting bot: {bot_name} (ID: {bot_id})")
            p = subprocess.Popen([sys.executable, "discord_llama.py", bot_id])
            processes.append(p)

        print(f"All {len(processes)} bots started. Press Ctrl+C to stop.")

        while True:
            time.sleep(1)
            for p in list(processes):
                if p.poll() is not None:
                    print(f"A bot process (PID {p.pid}) has exited.")
                    processes.remove(p)
                    if not processes:
                        print("All bots have exited. Exiting manager.")
                        return

    except Exception as e:
        print(f"An error occurred: {e}")
        signal_handler(None, None)


if __name__ == "__main__":
    main()
