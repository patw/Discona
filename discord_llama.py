import discord
import re
import random
import sys
import os
import asyncio
import logging
from openai import OpenAI
from dotenv import load_dotenv
from moofile import Collection

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('discona')

data_dir = os.getenv('DATA_DIR', '.')
bots_col = Collection(os.path.join(data_dir, 'bots.bson'), indexes=['name'], readonly=True)
rels_col = Collection(os.path.join(data_dir, 'relationships.bson'), indexes=['bot_id', 'name'], readonly=True)
config_col = Collection(os.path.join(data_dir, 'system_config.bson'), readonly=True)

if len(sys.argv) != 2:
    print("Usage: python discord_llama.py <bot_id>")
    print("Make sure you have the llama.cpp server running already and your model.json points to it.")
    print("You must also have a pre-configured bot in discord applications:")
    print("https://discord.com/developers/applications")
    sys.exit(1)

bot_id = sys.argv[1]
bot_data = bots_col.find_one({'_id': bot_id})
if not bot_data:
    print(f"Error: Bot ID {bot_id} not found.")
    sys.exit(1)

discord_token = bot_data.get("discord_api_key")
if not discord_token:
    print(f"Error: Bot {bot_id} has no discord_api_key configured.")
    sys.exit(1)

QUESTION_PROMPT = "Context:\n{history}\n\n{user} asks: {question}\n\nReply:"
TRIGGER_PROMPT = "Context:\n{history}\n\n{user} says: {question}\n\nReply (optional, stay in character):"

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Discord mentions come in three forms: <@123>, <@!123> (nickname), <@&123> (role).
MENTION_RE = re.compile(r'<@!?&?\d+>')
# Only strip the '@' from real room mentions so a bot can never ping a whole channel.
EVERYONE_MENTION_RE = re.compile(r'@(?=(?:here|everyone|channel)\b)', re.IGNORECASE)


def remove_id(text):
    """Strip <@id>/<@!id>/<@&id> mention syntax from a message."""
    return MENTION_RE.sub('', text).strip()


def strip_everyone_mentions(text):
    """Turn @here/@everyone/@channel into plain words (no accidental pings)."""
    return EVERYONE_MENTION_RE.sub('', text)


def format_prompt(prompt, user, question, history):
    return (prompt
            .replace("{user}", user)
            .replace("{question}", question)
            .replace("{history}", history))


def split_message(message, limit=2000):
    """Split into <=limit chunks, preferring newline boundaries and keeping
    ``` code fences balanced across messages so Discord doesn't render the
    rest of a chunk as code."""
    if len(message) <= limit:
        return [message]

    # Leave headroom for the '```\n' / '\n```' markers the fence balancer may add.
    effective = limit - 8

    pieces = []
    while len(message) > effective:
        cut = message.rfind('\n', 0, effective)
        if cut < effective // 2:
            cut = effective
        pieces.append(message[:cut])
        message = message[cut:].lstrip('\n')
    if message:
        pieces.append(message)

    # Balance code fences across chunk boundaries.
    out = []
    in_fence = False
    for p in pieces:
        n = p.count('```')
        start = in_fence
        in_fence = bool((n % 2 == 1) ^ start)   # odd count toggles the fence state
        end = in_fence
        if start:
            p = '```\n' + p
        if end:
            p = p + '\n```'
        out.append(p)
    return out


# Cache OpenAI clients per (base_url, api_key) — creating one per message leaks
# HTTP connections and is pure overhead. Config edits just build a new client.
_openai_clients = {}


def _get_openai_client(llm_cfg):
    key = (llm_cfg["base_url"], llm_cfg["api_key"])
    openai_client = _openai_clients.get(key)
    if openai_client is None:
        openai_client = OpenAI(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])
        _openai_clients[key] = openai_client
    return openai_client


def llm_local(prompt, system_prompt, llm_cfg):
    openai_client = _get_openai_client(llm_cfg)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    temp = float(llm_cfg.get("temperature", 0.7))
    response = openai_client.chat.completions.create(
        model=llm_cfg["model"], max_tokens=2000, temperature=temp, messages=messages
    )
    return response.choices[0].message.content


def build_identity(bot_data):
    """Build the system prompt from bot fields, skipping empty sections."""
    parts = []
    if bot_data.get('backstory'):
        parts.append(bot_data['backstory'])
    if bot_data.get('personality'):
        parts.append(bot_data['personality'])
    if bot_data.get('writing_sample'):
        parts.append(f"Writing style (always write in this style):\n{bot_data['writing_sample']}")
    return "\n\n".join(parts)


@client.event
async def on_ready():
    print(f'Bot logged in as {client.user}')


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Re-read bot data on every message so edits take effect live
    bot_data = bots_col.find_one({'_id': bot_id})
    if not bot_data:
        print(f"Bot {bot_id} no longer in database, skipping.")
        return

    if bot_data.get('enabled', True) is False:
        return  # Disabled from the web UI — stay connected but stay quiet.

    sys_config = config_col.find_one({'_id': 'config'})
    if not sys_config:
        print("System config not found, skipping.")
        return

    triggers = [w.strip() for w in (bot_data.get("trigger_words") or "").split(",") if w.strip()]
    try:
        trigger_level = float(bot_data.get("activity_level") or 0.0)
    except (TypeError, ValueError):
        trigger_level = 0.0
    try:
        history_lines = int(sys_config.get("history_lines") or 10)
    except (TypeError, ValueError):
        history_lines = 10

    # Per-bot overrides fall back to the system config.
    model = bot_data.get("model_name") or sys_config.get("model_name")
    try:
        temp = float(bot_data.get("temperature")) if bot_data.get("temperature") is not None \
            else float(sys_config.get("default_temperature") or 0.7)
    except (TypeError, ValueError):
        temp = 0.7
    llm_cfg = {
        "base_url": sys_config["openai_base_url"],
        "api_key": sys_config["openai_api_key"],
        "model": model,
        "temperature": temp,
    }

    mentioned = client.user.mentioned_in(message)
    # Word-boundary, case-insensitive trigger matching: "cat" no longer matches
    # "concatenate", and "Vector" matches a lowercase "vector" in chat.
    comment_on_it = any(
        re.search(r'(?<!\w)' + re.escape(w) + r'(?!\w)', message.content, re.IGNORECASE)
        for w in triggers
    )

    # Cheap gates first — never fetch history or hit the LLM unless we're going to reply.
    if mentioned:
        mode = 'direct'
    elif comment_on_it and random.random() <= trigger_level:
        mode = 'trigger'
    else:
        return

    relationship_context = ""
    rel = rels_col.find_one({'bot_id': bot_id, 'name': message.author.name})
    if rel:
        relationship_context = f"\n\nFacts about {message.author.name}:\n{rel['facts']}"

    system_prompt = build_identity(bot_data) + relationship_context

    # Conversation history — only the messages before this one (before= avoids
    # the old content-compare hack that dropped identical consecutive messages).
    history_list = []
    channel_history = [m async for m in message.channel.history(limit=history_lines, before=message)]
    for hist in channel_history:
        history_list.append(f"{hist.author.name}: {remove_id(hist.content)}")
    history_list.reverse()
    history_text = '\n'.join(history_list)

    prompt = format_prompt(
        QUESTION_PROMPT if mode == 'direct' else TRIGGER_PROMPT,
        message.author.name,
        remove_id(message.content),
        history_text,
    )

    # The LLM call blocks for a long time — run it off the event loop so the
    # Discord gateway heartbeat isn't starved (otherwise slow responses cause
    # random disconnects). Show a typing indicator while we wait.
    try:
        async with message.channel.typing():
            bot_response = await asyncio.to_thread(llm_local, prompt, system_prompt, llm_cfg)
    except Exception as e:
        log.exception("LLM call failed for bot %s: %s", bot_id, e)
        return

    bot_response = strip_everyone_mentions(bot_response or "")
    for chunk in split_message(bot_response):
        if mode == 'direct':
            await message.reply(chunk, mention_author=False)
        else:
            await message.channel.send(chunk)


client.run(discord_token)
