import discord
import re
import random
import sys
import os
from openai import OpenAI
from dotenv import load_dotenv
from moofile import Collection

load_dotenv()

data_dir = os.getenv('DATA_DIR', '.')
bots_col = Collection(os.path.join(data_dir, 'bots.bson'), indexes=['name'])
rels_col = Collection(os.path.join(data_dir, 'relationships.bson'), indexes=['bot_id', 'name'])
config_col = Collection(os.path.join(data_dir, 'system_config.bson'))

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

bot_config = {
    "discord_token": bot_data["discord_api_key"],
    "identity": f"{bot_data.get('backstory', '')}\n\n{bot_data.get('personality', '')}\n\n{bot_data.get('writing_sample', '')}",
    "triggers": [w.strip() for w in bot_data["trigger_words"].split(",")] if bot_data.get("trigger_words") else [],
    "trigger_level": bot_data.get("activity_level") or 0.0,
    "history_lines": 10,
    "question_prompt": "Background:\n{identity}\n\nContext:\n{history}\n\n{user} asks: {question}\n\nReply:",
    "trigger_prompt": "Background:\n{identity}\n\nContext:\n{history}\n\n{user} says: {question}\n\nReply (optional, stay in character):",
}

sys_config = config_col.find_one({'_id': 'config'})
if not sys_config:
    print("Error: System config not found.")
    sys.exit(1)

llm_config = {
    "base_url": sys_config["openai_base_url"],
    "api_key": sys_config["openai_api_key"],
    "model": sys_config["model_name"],
    "temperature": sys_config["default_temperature"],
}
bot_config["history_lines"] = sys_config.get("history_lines") or 10

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def remove_id(text):
    return re.sub(r'<@\d+>', '', text)


def filter_mentions(text):
    return re.sub(r'[@]?(\b(here|everyone|channel)\b)', '', text)


def format_prompt(prompt, user, question, history):
    return (prompt
            .replace("{user}", user)
            .replace("{question}", question)
            .replace("{history}", history))


def split_message(message):
    return [message[i:i+2000] for i in range(0, len(message), 2000)]


def llm_local(prompt, system_prompt):
    openai_client = OpenAI(api_key=llm_config["api_key"], base_url=llm_config["base_url"])
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    temp = float(llm_config.get("temperature", 0.7))
    response = openai_client.chat.completions.create(
        model=llm_config["model"], max_tokens=2000, temperature=temp, messages=messages
    )
    return response.choices[0].message.content


@client.event
async def on_ready():
    print(f'Bot logged in as {client.user}')


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    history_list = []
    channel_history = [m async for m in message.channel.history(limit=bot_config["history_lines"] + 1)]
    for hist in channel_history:
        if remove_id(hist.content) != remove_id(message.content):
            history_list.append(hist.author.name + ": " + remove_id(hist.content))
    history_list.reverse()
    history_text = '\n'.join(history_list)

    direct_msg = False

    relationship_context = ""
    rel = rels_col.find_one({'bot_id': bot_id, 'name': message.author.name})
    if rel:
        relationship_context = f"\n\nFacts about {message.author.name}:\n{rel['facts']}"

    current_identity = bot_config["identity"] + relationship_context

    if client.user.mentioned_in(message):
        prompt = format_prompt(
            bot_config["question_prompt"],
            message.author.name,
            remove_id(message.content),
            history_text,
        )
        prompt = prompt.replace("{identity}", current_identity)
        direct_msg = True
        bot_response = filter_mentions(llm_local(prompt, current_identity))
        for chunk in split_message(bot_response):
            await message.channel.send(chunk)

    comment_on_it = any(word in message.content for word in bot_config["triggers"])
    if comment_on_it and random.random() <= float(bot_config["trigger_level"]) and not direct_msg:
        prompt = format_prompt(
            bot_config["trigger_prompt"],
            message.author.name,
            remove_id(message.content),
            history_text,
        )
        prompt = prompt.replace("{identity}", current_identity)
        bot_response = filter_mentions(llm_local(prompt, current_identity))
        for chunk in split_message(bot_response):
            await message.channel.send(chunk)


client.run(bot_config["discord_token"])
