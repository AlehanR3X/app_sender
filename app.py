import os
import uuid
import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, render_template
from telethon import TelegramClient, errors
from config import (
    api_id, api_hash, SESSION_NAME, SLEEP_TIME,
    bot_username, bot2_username, bot3_user, bot4_user,
    GROUP_CHAT_ID, GROUP_CHAT2_ID
)

# Configuración de Flask
tpl = Flask(__name__)
tpl.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cambia_esto_por_un_valor_seguro')

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ThreadPool para tareas de envío
eecutor = ThreadPoolExecutor(max_workers=int(os.environ.get('MAX_WORKERS', 5)))
# Estructura para almacenar estado de tareas
tasks = {}

def validate_destination(dest_key):
    mapping = {
        "bot_username": bot_username,
        "bot2_username": bot2_username,
        "bot3_user": bot3_user,
        "bot4_user": bot4_user,
        "GROUP_CHAT_ID": GROUP_CHAT_ID,
        "GROUP_CHAT2_ID": GROUP_CHAT2_ID
    }
    return mapping.get(dest_key)

class MessageTask:
    def __init__(self, job_id, prefix, lines, destination, sleep_time):
        self.job_id = job_id
        self.prefix = prefix.strip()
        self.lines = lines
        self.destination = destination
        self.sleep_time = sleep_time
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.total = len(lines)
        self.sent = 0
        self.status = 'pending'  # pending, running, paused, stopped, completed        
        tasks[job_id] = self

    def run(self):
        self.status = 'running'
        asyncio.new_event_loop().run_until_complete(self._send())
        if self.sent >= self.total:
            self.status = 'completed'
        elif self.stop_event.is_set():
            self.status = 'stopped'
        else:
            self.status = 'paused'
        logger.info(f"Task {self.job_id} ended with status: {self.status}")

    async def _send(self):
        async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
            try:
                if not await client.is_user_authorized():
                    await client.start()
            except errors.PasswordHashInvalidError:
                logger.error(f"2FA invalida para {self.job_id}")
                return
            except Exception as e:
                logger.error(f"Error al iniciar sesión [{self.job_id}]: {e}")
                return

            for line in self.lines:
                if self.stop_event.is_set():
                    break
                while self.pause_event.is_set():
                    self.status = 'paused'
                    await asyncio.sleep(0.5)
                self.status = 'running'
                message = f"{self.prefix} {line}"
                try:
                    await client.send_message(self.destination, message)
                    self.sent += 1
                    logger.info(f"[{self.job_id}] Enviado: {message}")
                except Exception as e:
                    logger.error(f"[{self.job_id}] Error al enviar: {e}")
                await asyncio.sleep(self.sleep_time)

# Rutas de la aplicación
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_sending():
    data = request.get_json()
    prefix = data.get('prefix', '')
    lines = data.get('lines', [])
    dest_key = data.get('destination', '')
    sleep_time = int(data.get('sleep_time', SLEEP_TIME))

    # Validaciones básicas
    if not prefix or not isinstance(lines, list) or not lines or not all(isinstance(l, str) for l in lines):
        return jsonify({'error': 'Prefijo y líneas válidas son requeridos.'}), 400
    destination = validate_destination(dest_key)
    if not destination:
        return jsonify({'error': 'Destino inválido.'}), 400

    # Crear y lanzar tarea
    job_id = str(uuid.uuid4())
    task = MessageTask(job_id, prefix, lines, destination, sleep_time)
    executor.submit(task.run)

    return jsonify({'status': 'started', 'job_id': job_id}), 202

@app.route('/pause', methods=['POST'])
def pause_sending():
    data = request.get_json()
    job_id = data.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    task.pause_event.set()
    return jsonify({'status': 'paused'}), 200

@app.route('/resume', methods=['POST'])
def resume_sending():
    data = request.get_json()
    job_id = data.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    task.pause_event.clear()
    return jsonify({'status': 'running'}), 200

@app.route('/stop', methods=['POST'])
def stop_sending():
    data = request.get_json()
    job_id = data.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    task.stop_event.set()
    return jsonify({'status': 'stopped'}), 200

@app.route('/progress', methods=['GET'])
def progress():
    job_id = request.args.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    percent = (task.sent / task.total * 100) if task.total else 0
    return jsonify({
        'job_id': job_id,
        'status': task.status,
        'sent': task.sent,
        'total': task.total,
        'percent': round(percent, 2)
    }), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    tpl.run(host='0.0.0.0', port=port, debug=False)
