from flask import Flask, request, jsonify, render_template, session
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
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'cambia_esto_por_un_valor_seguro')

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
                await client.start()
        except errors.PasswordHashInvalidError:
            # La contraseña de 2FA debería haberse ingresado via endpoint separado
            return
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
    sleep_time = int(data.get('sleep_time', SLEEP_TIME))

    # Validaciones
    if not prefix or not isinstance(lines, list) or not all(isinstance(l, str) for l in lines):
        return jsonify({'error': 'El prefijo y las líneas son requeridos y deben ser texto.'}), 400
    if not destination:
        return jsonify({'error': 'Destino requerido'}), 400
    if sending_thread and sending_thread.is_alive():
        return jsonify({'error': 'Ya hay un envío en curso.'}), 400

    # Mapear destinos a valores reales
    destinations = {
        "bot_username": bot_username,
        "bot2_username": bot2_username,
        "bot3_user": bot3_user,
        "bot4_user": bot4_user,
        "GROUP_CHAT_ID": GROUP_CHAT_ID,
        "GROUP_CHAT2_ID": GROUP_CHAT2_ID
    }
    destination = destinations.get(destination, destination)

    # Iniciar hilo de envío
    def run_async():
        asyncio.new_event_loop().run_until_complete(
            send_messages(prefix, lines, destination, sleep_time)
        )
    sending_thread = threading.Thread(target=run_async)
    sending_thread.start()
    return jsonify({'status': 'Envío iniciado'})

# Endpoint para pausar/reanudar
@app.route('/pause', methods=['POST'])
def pause_sending():
    if pause_event.is_set():
        pause_event.clear()
        return jsonify({'status': 'Reanudado'})
    else:
        pause_event.set()
        return jsonify({'status': 'Pausado'})

# Endpoint para detener
@app.route('/stop', methods=['POST'])
def stop_sending():
    stop_event.set()
    return jsonify({'status': 'Detenido'})

# Endpoint para autenticar
@app.route('/authenticate', methods=['POST'])
def authenticate():
    data = request.json
    phone = data.get('phone_number')
    code = data.get('verification_code')

    # Guardar teléfono en sesión si se proporciona
    if phone:
        session['phone'] = phone
    elif 'phone' not in session:
        return jsonify({'error': 'Número de teléfono no especificado'}), 400

    async def auth_flow():
        async with TelegramClient(SESSION_NAME, api_id, api_hash) as client:
            try:
                if not await client.is_user_authorized():
                    if not code:
                        # Enviar código
                        await client.send_code_request(session['phone'])
                        return {'status': 'Código de verificación enviado'}
                    else:
                        # Firmar con código
                        await client.sign_in(phone=session['phone'], code=code)
                        return {'status': 'Autenticado correctamente'}
                return {'status': 'Ya autenticado'}
            except errors.SessionPasswordNeededError:
                return {'error': 'Se requiere contraseña de segundo factor'}
            except Exception as e:
                return {'error': f'Error al autenticar: {e}'}

    result = asyncio.run(auth_flow())
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
