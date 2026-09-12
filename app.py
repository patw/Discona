from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_bootstrap import Bootstrap5
import os
import uuid
import hmac
import time
from dotenv import load_dotenv
from moofile import Collection

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret')
if app.secret_key == 'dev_secret':
    print('WARNING: SECRET_KEY is not set — using the insecure default. Set SECRET_KEY in .env.')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Strict',
)
admin_password = os.getenv('ADMIN_PASSWORD', 'admin')
bootstrap = Bootstrap5(app)

data_dir = os.getenv('DATA_DIR', '.')

# Values accepted for the system "Model Reasoning" setting; forwarded verbatim
# to the backend as `reasoning_effort` ("none" disables hidden reasoning).
REASONING_EFFORTS = ('none', 'low', 'medium', 'high')

bots_col = Collection(os.path.join(data_dir, 'bots.bson'), indexes=['name'])
rels_col = Collection(os.path.join(data_dir, 'relationships.bson'), indexes=['bot_id', 'name'])
config_col = Collection(os.path.join(data_dir, 'system_config.bson'))

if not config_col.exists({'_id': 'config'}):
    config_col.insert({
        '_id': 'config',
        'openai_base_url': 'http://localhost:8080/v1',
        'openai_api_key': 'sk-no-key-required',
        'model_name': 'llama-3-8b',
        'default_temperature': 0.7,
        'history_lines': 10,
        'max_tokens': 4000,
        'reasoning_effort': 'none',
    })


def _parse_float(value, default, label):
    """Parse a form float field; blank -> default, garbage -> ValueError."""
    if value is None or str(value).strip() == '':
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a number.')


def _parse_int(value, default, label):
    if value is None or str(value).strip() == '':
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a whole number.')


def _parse_activity_level(value):
    """Blank -> None, otherwise a float in [0, 1] or a ValueError."""
    level = _parse_float(value, None, 'Activity level')
    if level is not None and not (0.0 <= level <= 1.0):
        raise ValueError('Activity level must be between 0 and 1.')
    return level


@app.before_request
def _api_auth():
    """Gate the machine-readable endpoints behind an optional shared token.

    These endpoints used to be fully public and returned secrets (Discord bot
    tokens, the LLM API key). Secrets are now scrubbed from their responses;
    set API_TOKEN in .env to restrict access entirely.
    """
    if request.path.startswith(('/list_bots', '/get_bot', '/get_relationship', '/get_system_config')):
        token = os.getenv('API_TOKEN')
        if token and request.args.get('token') != token:
            return jsonify({'error': 'Unauthorized'}), 401


@app.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    bots = bots_col.find({}).to_list()
    return render_template('index.html', bots=bots)


@app.route('/new_bot', methods=['GET', 'POST'])
def new_bot():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            try:
                activity_level = _parse_activity_level(request.form.get('activity_level'))
                temperature = _parse_float(request.form.get('temperature'), None, 'Temperature')
                if temperature is not None and not (0.0 <= temperature <= 2.0):
                    raise ValueError('Temperature must be between 0 and 2.')
            except ValueError as e:
                return render_template('new_bot.html', error=str(e))
            bots_col.insert({
                '_id': str(uuid.uuid4()),
                'name': name,
                'description': request.form.get('description'),
                'backstory': request.form.get('backstory'),
                'personality': request.form.get('personality'),
                'writing_sample': request.form.get('writing_sample'),
                'discord_api_key': request.form.get('discord_api_key'),
                'trigger_words': request.form.get('trigger_words'),
                'activity_level': activity_level,
                'model_name': request.form.get('model_name') or None,
                'temperature': temperature,
                'enabled': True,
            })
            return redirect(url_for('index'))
    return render_template('new_bot.html')


@app.route('/bot/<bot_id>/relationships')
def bot_relationships(bot_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    bot = bots_col.find_one({'_id': bot_id})
    if not bot:
        return redirect(url_for('index'))
    rels = rels_col.find({'bot_id': bot_id}).to_list()
    return render_template('relationships.html', bot=bot, relationships=rels)


@app.route('/bot/<bot_id>/relationships/new', methods=['GET', 'POST'])
def new_relationship(bot_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    bot = bots_col.find_one({'_id': bot_id})
    if not bot:
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            rels_col.insert({
                '_id': str(uuid.uuid4()),
                'bot_id': bot_id,
                'name': name,
                'facts': request.form.get('facts'),
            })
            return redirect(url_for('bot_relationships', bot_id=bot_id))
    return render_template('new_relationship.html', bot=bot)


@app.route('/bot/<bot_id>/edit', methods=['GET', 'POST'])
def edit_bot(bot_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    bot = bots_col.find_one({'_id': bot_id})
    if not bot:
        return redirect(url_for('index'))
    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            try:
                activity_level = _parse_activity_level(request.form.get('activity_level'))
                temperature = _parse_float(request.form.get('temperature'), None, 'Temperature')
                if temperature is not None and not (0.0 <= temperature <= 2.0):
                    raise ValueError('Temperature must be between 0 and 2.')
            except ValueError as e:
                return render_template('edit_bot.html', bot=bot, error=str(e))
            bots_col.update_one(where={'_id': bot_id}, set={
                'name': name,
                'description': request.form.get('description'),
                'backstory': request.form.get('backstory'),
                'personality': request.form.get('personality'),
                'writing_sample': request.form.get('writing_sample'),
                'discord_api_key': request.form.get('discord_api_key'),
                'trigger_words': request.form.get('trigger_words'),
                'activity_level': activity_level,
                'model_name': request.form.get('model_name') or None,
                'temperature': temperature,
            })
            return redirect(url_for('index'))
    return render_template('edit_bot.html', bot=bot)


@app.route('/bot/<bot_id>/relationship/<rel_id>/edit', methods=['GET', 'POST'])
def edit_relationship(bot_id, rel_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    bot = bots_col.find_one({'_id': bot_id})
    rel = rels_col.find_one({'_id': rel_id, 'bot_id': bot_id})
    if not bot or not rel:
        return redirect(url_for('bot_relationships', bot_id=bot_id))
    if request.method == 'POST':
        name = request.form.get('name')
        if name:
            rels_col.update_one(where={'_id': rel_id}, set={
                'name': name,
                'facts': request.form.get('facts'),
            })
            return redirect(url_for('bot_relationships', bot_id=bot_id))
    return render_template('edit_relationship.html', bot=bot, rel=rel)


@app.route('/bot/<bot_id>/delete', methods=['POST'])
def delete_bot(bot_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    rels_col.delete_many({'bot_id': bot_id})
    bots_col.delete_one({'_id': bot_id})
    return redirect(url_for('index'))


@app.route('/bot/<bot_id>/toggle', methods=['POST'])
def toggle_bot(bot_id):
    """Enable/disable a bot without deleting it. Takes effect on the next
    message the bot runner sees."""
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    bot = bots_col.find_one({'_id': bot_id})
    if bot:
        bots_col.update_one(where={'_id': bot_id}, set={'enabled': not bot.get('enabled', True)})
    return redirect(url_for('index'))


@app.route('/bot/<bot_id>/relationship/<rel_id>/delete', methods=['POST'])
def delete_relationship(bot_id, rel_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    rels_col.delete_one({'_id': rel_id, 'bot_id': bot_id})
    return redirect(url_for('bot_relationships', bot_id=bot_id))


@app.route('/list_bots')
def list_bots():
    bots = bots_col.find({}).to_list()
    return jsonify([{'id': b['_id'], 'name': b['name'], 'enabled': b.get('enabled', True)} for b in bots])


@app.route('/get_bot/<bot_id>')
def get_bot(bot_id):
    bot = bots_col.find_one({'_id': bot_id})
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    # Note: discord_api_key is intentionally NOT exposed here (it used to be
    # public to anyone who could reach this server). The bot runner reads the
    # data file directly, so no tooling needs it over HTTP.
    return jsonify({
        'id': bot['_id'],
        'name': bot['name'],
        'description': bot.get('description'),
        'backstory': bot.get('backstory'),
        'personality': bot.get('personality'),
        'writing_sample': bot.get('writing_sample'),
        'trigger_words': bot.get('trigger_words'),
        'activity_level': bot.get('activity_level'),
        'model_name': bot.get('model_name'),
        'temperature': bot.get('temperature'),
        'enabled': bot.get('enabled', True),
    })


@app.route('/get_relationship/<bot_id>/<name>')
def get_relationship(bot_id, name):
    rel = rels_col.find_one({'bot_id': bot_id, 'name': name})
    if not rel:
        return jsonify({'error': 'Relationship not found'}), 404
    return jsonify({'name': rel['name'], 'facts': rel['facts']})


@app.route('/system', methods=['GET', 'POST'])
def system():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    if request.method == 'POST':
        try:
            temp = _parse_float(request.form.get('default_temperature'), 0.7, 'Default temperature')
            hist = _parse_int(request.form.get('history_lines'), 10, 'History lines')
            max_tokens = _parse_int(request.form.get('max_tokens'), 4000, 'Max response tokens')
            reasoning_effort = request.form.get('reasoning_effort', 'none')
            if not (0.0 <= temp <= 2.0):
                raise ValueError('Default temperature must be between 0 and 2.')
            if hist < 0:
                raise ValueError('History lines cannot be negative.')
            if not (256 <= max_tokens <= 32000):
                raise ValueError('Max response tokens must be between 256 and 32000.')
            if reasoning_effort not in REASONING_EFFORTS:
                raise ValueError('Reasoning must be one of: none, low, medium, high.')
        except ValueError as e:
            config = config_col.find_one({'_id': 'config'})
            return render_template('system.html', config=config, error=str(e))
        config_col.update_one(where={'_id': 'config'}, set={
            'openai_base_url': request.form.get('openai_base_url'),
            'openai_api_key': request.form.get('openai_api_key'),
            'model_name': request.form.get('model_name'),
            'default_temperature': temp,
            'history_lines': hist,
            'max_tokens': max_tokens,
            'reasoning_effort': reasoning_effort,
        })
        return redirect(url_for('system'))
    config = config_col.find_one({'_id': 'config'})
    return render_template('system.html', config=config)


@app.route('/get_system_config')
def get_system_config():
    config = config_col.find_one({'_id': 'config'})
    # The LLM api_key is intentionally not exposed here (it used to be public
    # to anyone who could reach this server).
    return jsonify({
        'base_url': config['openai_base_url'],
        'model': config['model_name'],
        'temperature': config['default_temperature'],
        'history_lines': config.get('history_lines') or 10,
        'max_tokens': config.get('max_tokens') or 4000,
        'reasoning_effort': config.get('reasoning_effort', 'none'),
    })


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        now = time.time()
        if now < session.get('lockout_until', 0):
            return render_template('login.html', error='Too many attempts. Try again in 30 seconds.')
        if hmac.compare_digest(request.form.get('password', ''), admin_password):
            session.clear()
            session['logged_in'] = True
            session['login_attempts'] = 0
            return redirect(url_for('index'))
        attempts = session.get('login_attempts', 0) + 1
        if attempts >= 5:
            session['lockout_until'] = now + 30
            session['login_attempts'] = 0
            error = 'Too many failed attempts. Try again in 30 seconds.'
        else:
            session['login_attempts'] = attempts
            error = 'Invalid password'
        return render_template('login.html', error=error)
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(port=5001, host='0.0.0.0', debug=False)
