from flask import Flask, request, jsonify, render_template
from telethon import TelegramClient, errors
import asyncio
import threading
from config import (
    api_id, api_hash, SESSION_NAME, SLEEP_TIME,
    bot_username, bot2_username, bot3_user, bot4_user,
    GROUP_CHAT_ID, GROUP_CHAT2_ID
)
import os

app = Flask(__name__)

# Variables globales para el control del envío
sending_thread = None
stop_event = threading.Event()
pause_event = threading.Event()

# Ruta para servir la página principal
@app.route('/')
def index():
    return render_template('index.html')

# Función para enviar mensajes
async def send_messages(prefix, lines, destination, sleep_time):
    async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
        try:
            if not await client.is_user_authorized():
                print("Autenticación requerida. Iniciando sesión...")
                await client.start()
        except errors.PasswordHashInvalidError:
            print("Se requiere contraseña de verificación en dos pasos.")
            password = input("Introduce la contraseña de verificación en dos pasos: ")
            await client.sign_in(password=password)
        except Exception as e:
            print(f"Error al iniciar sesión: {e}")
            return

        for i, line in enumerate(lines):
            if stop_event.is_set():
                break
            while pause_event.is_set():
                await asyncio.sleep(0.5)
            message = f"{prefix} {line}"
            try:
                await client.send_message(destination, message)
                print(f"Enviado: {message}")
            except Exception as e:
                print(f"Error al enviar a {destination}: {e}")
            await asyncio.sleep(sleep_time)

# Endpoint para iniciar el envío
@app.route('/start', methods=['POST'])
def start_sending():
    global sending_thread, stop_event, pause_event
    stop_event.clear()
    pause_event.clear()

    data = request.json
    prefix = data.get('prefix', '')
    lines = data.get('lines', [])
    destination = data.get('destination', '')
    sleep_time = int(data.get('sleep_time', SLEEP_TIME))  # Convertir a entero

    if not prefix or not lines or not destination:
        return jsonify({'error': 'Faltan datos necesarios'}), 400

    # Validar el destino
    destinations = {
        "bot_username": bot_username,
        "bot2_username": bot2_username,
        "bot3_user": bot3_user,
        "bot4_user": bot4_user,
        "GROUP_CHAT_ID": GROUP_CHAT_ID,
        "GROUP_CHAT2_ID": GROUP_CHAT2_ID
    }
    destination = destinations.get(destination, destination)

    # Iniciar el envío en un hilo separado
    sending_thread = threading.Thread(target=asyncio.run, args=(send_messages(prefix, lines, destination, sleep_time),))
    sending_thread.start()
    return jsonify({'status': 'Envío iniciado'})

# Endpoint para pausar o reanudar el envío
@app.route('/pause', methods=['POST'])
def pause_sending():
    if pause_event.is_set():
        pause_event.clear()
        return jsonify({'status': 'Reanudado'})
    else:
        pause_event.set()
        return jsonify({'status': 'Pausado'})

# Endpoint para detener el envío
@app.route('/stop', methods=['POST'])
def stop_sending():
    stop_event.set()
    return jsonify({'status': 'Detenido'})

# Endpoint para autenticar la cuenta de Telegram
@app.route('/authenticate', methods=['POST'])
def authenticate():
    data = request.json
    phone_number = data.get('phone_number', '')
    verification_code = data.get('verification_code', '')

    if not phone_number:
        return jsonify({'error': 'Número de teléfono requerido'}), 400

    async def authenticate_user():
        async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
            try:
                if not await client.is_user_authorized():
                    await client.send_code_request(phone_number)
                    if verification_code:
                        await client.sign_in(phone_number, verification_code)
                        return {'status': 'Autenticado correctamente'}
                    else:
                        return {'status': 'Código de verificación enviado'}
                else:
                    return {'status': 'Ya autenticado'}
            except errors.SessionPasswordNeededError:
                return {'error': 'Se requiere contraseña de verificación en dos pasos'}
            except Exception as e:
                return {'error': f'Error al autenticar: {e}'}

    result = asyncio.run(authenticate_user())
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
