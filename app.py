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
from live_config import GROUPS  # Importar los grupos desde live_config.py
import sqlite3

# Configuración de Flask
tpl = Flask(__name__)
tpl.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cambia_esto_por_un_valor_seguro')

# Logging
logging.basicConfig(
    level=logging.DEBUG,  # Cambiar a DEBUG
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ThreadPool para tareas de envío
executor = ThreadPoolExecutor(max_workers=int(os.environ.get('MAX_WORKERS', 5)))
# Estructura para almacenar estado de tareas
tasks = {}

# Variable global para rastrear si hay una tarea activa
active_task = None
active_task_lock = threading.Lock()

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

# Crear una instancia global de TelegramClient
telegram_client = TelegramClient(SESSION_NAME, api_id, api_hash)

# Inicializar la sesión al inicio de la aplicación
async def initialize_telegram_client():
    try:
        await telegram_client.connect()
        if not await telegram_client.is_user_authorized():
            await telegram_client.start()
        logger.info("Cliente de Telegram inicializado correctamente.")
    except Exception as e:
        logger.error(f"Error al inicializar el cliente de Telegram: {e}")
        raise e  # Lanzar el error para que sea manejado adecuadamente

# Llamar a la inicialización al inicio de la aplicación
try:
    asyncio.run(initialize_telegram_client())
except Exception as e:
    logger.error("No se pudo inicializar el cliente de Telegram. Verifica las credenciales y la conexión.")
    exit(1)  # Salir si no se puede inicializar el cliente

async def ensure_telegram_connection():
    try:
        if not telegram_client.is_connected():
            await telegram_client.connect()
            if not await telegram_client.is_user_authorized():
                await telegram_client.start()
            logger.info("Reconexión automática del cliente de Telegram exitosa.")
    except Exception as e:
        logger.error(f"Error al reconectar el cliente de Telegram: {e}")
        raise e  # Lanzar el error para manejarlo adecuadamente

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
        logger.debug(f"Iniciando tarea {self.job_id} con destino {self.destination}")
        loop = asyncio.get_event_loop()
        future = asyncio.run_coroutine_threadsafe(self._send(), loop)
        try:
            future.result()  # Bloquea hasta que la tarea termine
        except Exception as e:
            logger.error(f"Error en la tarea [{self.job_id}]: {e}")
        if self.sent >= self.total:
            self.status = 'completed'
            logger.debug(f"Tarea {self.job_id} completada. Mensajes enviados: {self.sent}")
        elif self.stop_event.is_set():
            self.status = 'stopped'
            logger.debug(f"Tarea {self.job_id} detenida manualmente.")
        else:
            self.status = 'paused'
            logger.debug(f"Tarea {self.job_id} pausada.")
        logger.info(f"Task {self.job_id} ended with status: {self.status}")

    async def _send(self):
        try:
            if not telegram_client.is_connected():
                logger.debug("Reconectando cliente de Telegram...")
                await telegram_client.connect()
            for line in self.lines:
                if self.stop_event.is_set():
                    logger.debug(f"Tarea {self.job_id} detenida antes de enviar: {line}")
                    break
                while self.pause_event.is_set():
                    self.status = 'paused'
                    logger.debug(f"Tarea {self.job_id} pausada. Esperando reanudación...")
                    await asyncio.sleep(0.5)
                self.status = 'running'
                message = f"{self.prefix} {line}"
                try:
                    logger.debug(f"Enviando mensaje: {message}")
                    await telegram_client.send_message(self.destination, message)
                    self.sent += 1
                    logger.info(f"[{self.job_id}] Mensaje enviado: {message}")
                except Exception as e:
                    logger.error(f"[{self.job_id}] Error al enviar mensaje: {e}")
                await asyncio.sleep(self.sleep_time)
        except Exception as e:
            self.status = 'error'
            logger.error(f"Error en la tarea [{self.job_id}]: {e}")

# Variable global para manejar la tarea en modo live
live_task = None
live_task_lock = threading.Lock()

class LiveExtractionTask(threading.Thread):
    def run(self):
        logger.debug(f"Iniciando extracción en canal {self.channel} con límite {self.limit}")
        import time
        for i in range(self.limit):
            if self.stop_event.is_set():
                logger.debug(f"Extracción detenida en el mensaje {i + 1}")
                break
            logger.debug(f"Procesando mensaje {i + 1} del canal {self.channel}")
            time.sleep(1)  # Simula tiempo de procesamiento
        logger.info("Extracción finalizada.")

    def stop(self):
        logger.debug("Deteniendo tarea de extracción en tiempo real.")
        self.stop_event.set()

# Rutas de la aplicación
@tpl.route('/')
def index():
    return render_template('index.html')

@tpl.route('/start', methods=['POST'])
def start_sending():
    data = request.get_json()
    logger.debug(f"Datos recibidos para envío: {data}")  # Depuración de datos recibidos
    prefix = data.get('prefix', '')
    lines = data.get('lines', [])
    dest_key = data.get('destination', '')
    sleep_time = int(data.get('sleep_time', SLEEP_TIME))

    # Validaciones mejoradas
    if not prefix or len(prefix) > 50:
        logger.error("El prefijo es inválido o excede 50 caracteres.")
        return jsonify({'error': 'El prefijo es requerido y no debe exceder 50 caracteres.'}), 400
    if not isinstance(lines, list) or not lines or len(lines) > 1000 or not all(isinstance(l, str) and len(l) <= 200 for l in lines):
        logger.error("Las líneas son inválidas o exceden el límite permitido.")
        return jsonify({'error': 'Las líneas deben ser una lista de hasta 1000 elementos, cada uno con un máximo de 200 caracteres.'}), 400
    destination = validate_destination(dest_key)
    if not destination:
        logger.error(f"Destino inválido: {dest_key}")
        return jsonify({'error': 'Destino inválido.'}), 400

    # Crear y lanzar tarea
    job_id = str(uuid.uuid4())
    logger.debug(f"Creando tarea con ID: {job_id}")
    task = MessageTask(job_id, prefix, lines, destination, sleep_time)
    executor.submit(task.run)

    return jsonify({'status': 'started', 'job_id': job_id}), 202

@tpl.route('/pause', methods=['POST'])
def pause_sending():
    data = request.get_json()
    job_id = data.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    task.pause_event.set()
    return jsonify({'status': 'paused'}), 200

@tpl.route('/resume', methods=['POST'])
def resume_sending():
    data = request.get_json()
    job_id = data.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    task.pause_event.clear()
    return jsonify({'status': 'running'}), 200

@tpl.route('/stop', methods=['POST'])
def stop_sending():
    global active_task

    data = request.get_json()
    job_id = data.get('job_id')
    task = tasks.get(job_id)
    if not task:
        return jsonify({'error': 'Job no encontrado.'}), 404
    task.stop_event.set()

    # Limpiar la tarea activa si se detiene
    with active_task_lock:
        if active_task and active_task.job_id == job_id:
            active_task = None

    return jsonify({'status': 'stopped'}), 200

@tpl.route('/progress', methods=['GET'])
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

@tpl.route('/live')
def live_page():
    return render_template('live.html')  # Renderiza la plantilla live.html

@tpl.route('/live/start', methods=['POST'])
def start_live():
    global live_task

    data = request.get_json()
    logger.debug(f"Datos recibidos para extracción en vivo: {data}")
    channel = data.get('channel')  # Aquí se recibe el chat_id del grupo
    limit = data.get('limit', 100)
    realtime = data.get('realtime', False)
    bank_filter = data.get('bank_filter', '')

    if not channel:
        logger.error("El grupo es obligatorio para iniciar la extracción.")
        return jsonify({'error': 'El grupo es obligatorio.'}), 400

    with live_task_lock:
        if live_task and live_task.is_alive():
            logger.error("Ya hay una tarea en ejecución.")
            return jsonify({'error': 'Ya hay una tarea en ejecución.'}), 400

        logger.debug(f"Iniciando tarea de extracción en vivo para el canal {channel}")
        live_task = LiveExtractionTask(channel, limit, realtime, bank_filter)
        live_task.start()

    return jsonify({'status': 'Tarea iniciada.'}), 202

@tpl.route('/live/stop', methods=['POST'])
def stop_live():
    global live_task

    with live_task_lock:
        if not live_task or not live_task.is_alive():
            return jsonify({'error': 'No hay ninguna tarea en ejecución.'}), 400

        live_task.stop()
        live_task = None

    return jsonify({'status': 'Tarea detenida.'}), 200

@tpl.route('/live/groups', methods=['GET'])
def get_groups():
    """Devuelve la lista de grupos disponibles para la extracción."""
    return jsonify(GROUPS), 200

@tpl.route('/live/data', methods=['GET'])
def get_extracted_data():
    """Devuelve los datos extraídos almacenados en la base de datos."""
    try:
        # Conectar a la base de datos SQLite
        conn = sqlite3.connect('messages.db')
        cursor = conn.cursor()

        # Consultar los datos extraídos
        cursor.execute('SELECT date, sender_id, chat_info, message FROM messages ORDER BY id DESC LIMIT 50')
        rows = cursor.fetchall()

        # Formatear los datos en una lista de diccionarios
        data = [
            {"date": row[0], "sender_id": row[1], "chat_info": row[2], "message": row[3]}
            for row in rows
        ]

        conn.close()
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Error al obtener los datos extraídos: {e}")
        return jsonify({'error': 'No se pudieron obtener los datos extraídos.'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    tpl.run(host='0.0.0.0', port=port, debug=False)
