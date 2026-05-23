from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_bootstrap import Bootstrap5
import os
import uuid
from dotenv import load_dotenv
from moofile import Collection

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev_secret')
admin_password = os.getenv('ADMIN_PASSWORD', 'admin')
bootstrap = Bootstrap5(app)

data_dir = os.getenv('DATA_DIR', '.')

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
    })


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
            activity_level_str = request.form.get('activity_level')
            bots_col.insert({
                '_id': str(uuid.uuid4()),
                'name': name,
                'description': request.form.get('description'),
                'backstory': request.form.get('backstory'),
                'personality': request.form.get('personality'),
                'writing_sample': request.form.get('writing_sample'),
                'discord_api_key': request.form.get('discord_api_key'),
                'trigger_words': request.form.get('trigger_words'),
                'activity_level': float(activity_level_str) if activity_level_str else None,
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
            activity_level_str = request.form.get('activity_level')
            bots_col.update_one(where={'_id': bot_id}, set={
                'name': name,
                'description': request.form.get('description'),
                'backstory': request.form.get('backstory'),
                'personality': request.form.get('personality'),
                'writing_sample': request.form.get('writing_sample'),
                'discord_api_key': request.form.get('discord_api_key'),
                'trigger_words': request.form.get('trigger_words'),
                'activity_level': float(activity_level_str) if activity_level_str else None,
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


@app.route('/bot/<bot_id>/relationship/<rel_id>/delete', methods=['POST'])
def delete_relationship(bot_id, rel_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    rels_col.delete_one({'_id': rel_id, 'bot_id': bot_id})
    return redirect(url_for('bot_relationships', bot_id=bot_id))


@app.route('/list_bots')
def list_bots():
    bots = bots_col.find({}).to_list()
    return jsonify([{'id': b['_id'], 'name': b['name']} for b in bots])


@app.route('/get_bot/<bot_id>')
def get_bot(bot_id):
    bot = bots_col.find_one({'_id': bot_id})
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    return jsonify({
        'id': bot['_id'],
        'name': bot['name'],
        'description': bot.get('description'),
        'backstory': bot.get('backstory'),
        'personality': bot.get('personality'),
        'writing_sample': bot.get('writing_sample'),
        'discord_api_key': bot.get('discord_api_key'),
        'trigger_words': bot.get('trigger_words'),
        'activity_level': bot.get('activity_level'),
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
        temp_str = request.form.get('default_temperature', '0.7')
        hist_str = request.form.get('history_lines', '10')
        config_col.update_one(where={'_id': 'config'}, set={
            'openai_base_url': request.form.get('openai_base_url'),
            'openai_api_key': request.form.get('openai_api_key'),
            'model_name': request.form.get('model_name'),
            'default_temperature': float(temp_str) if temp_str else 0.7,
            'history_lines': int(hist_str) if hist_str else 10,
        })
        return redirect(url_for('system'))
    config = config_col.find_one({'_id': 'config'})
    return render_template('system.html', config=config)


@app.route('/get_system_config')
def get_system_config():
    config = config_col.find_one({'_id': 'config'})
    return jsonify({
        'base_url': config['openai_base_url'],
        'api_key': config['openai_api_key'],
        'model': config['model_name'],
        'temperature': config['default_temperature'],
        'history_lines': config.get('history_lines') or 10,
    })


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == admin_password:
            session['logged_in'] = True
            return redirect(url_for('index'))
        return render_template('login.html', error='Invalid password')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


if __name__ == '__main__':
    app.run(port=5001, host='0.0.0.0', debug=False)
