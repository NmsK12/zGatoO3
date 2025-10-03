#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Certificados - WolfData Dox
Servidor especializado para consultas de antecedentes (penales, policiales, judiciales)
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import threading
from datetime import datetime, timedelta
from io import BytesIO

from flask import Flask, jsonify, request, send_file, make_response
from PIL import Image
from database_postgres import validate_api_key, init_database, register_api_key, delete_api_key
from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import MessageMediaPhoto

import config

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Variables globales
client = None
loop = None

def parse_antecedentes_response(text, tipo):
    """Parsea la respuesta de antecedentes (penales, policiales, judiciales)."""
    data = {}
    
    # Limpiar el texto de caracteres especiales
    clean_text = text.replace('**', '').replace('`', '').replace('*', '')
    
    # Extraer DNI
    dni_match = re.search(r'DNI\s*[➾\-=]\s*(\d+)', clean_text)
    if dni_match:
        data['DNI'] = dni_match.group(1)
    
    # Extraer nombres
    nombres_match = re.search(r'NOMBRES\s*[➾\-=]\s*([^\n\r]+)', clean_text)
    if nombres_match:
        data['NOMBRES'] = nombres_match.group(1).strip()
    
    # Extraer apellidos
    apellidos_match = re.search(r'APELLIDOS\s*[➾\-=]\s*([^\n\r]+)', clean_text)
    if apellidos_match:
        data['APELLIDOS'] = apellidos_match.group(1).strip()
    
    # Extraer género
    genero_match = re.search(r'GENERO\s*[➾\-=]\s*([^\n\r]+)', clean_text)
    if genero_match:
        data['GENERO'] = genero_match.group(1).strip()
    
    # Extraer edad
    edad_match = re.search(r'EDAD\s*[➾\-=]\s*(\d+)', clean_text)
    if edad_match:
        data['EDAD'] = edad_match.group(1)
    
    # Agregar tipo de certificado
    data['TIPO_CERTIFICADO'] = tipo
    
    return data

def check_connection():
    """Verifica la conexión de Telegram y reinicia si es necesario."""
    global client, loop
    
    try:
        if not client or not client.is_connected():
            logger.info("Cliente desconectado, intentando reconectar...")
            restart_telethon()
            return False
        return True
    except Exception as e:
        logger.error(f"Error verificando conexión: {str(e)}")
        restart_telethon()
        return False

def restart_telethon():
    """Reinicia el cliente de Telethon."""
    global client, loop
    
    try:
        if client:
            # Cerrar cliente existente de forma segura
            try:
                if loop and not loop.is_closed():
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(client.disconnect()))
                else:
                    # Si no hay loop disponible, simplemente marcar como desconectado
                    logger.warning("No hay loop disponible para desconectar cliente")
            except Exception as e:
                logger.warning(f"Error cerrando cliente anterior: {str(e)}")
            client = None
        
        # Esperar un poco antes de reiniciar
        import time
        time.sleep(2)
        
        # Reiniciar en un nuevo hilo
        init_telethon_thread()
        
        logger.info("Telethon reiniciado correctamente")
            
    except Exception as e:
        logger.error(f"Error reiniciando Telethon: {str(e)}")

def consult_antecedentes_sync(dni_number, tipo):
    """Consulta antecedentes usando Telethon de forma síncrona."""
    global client, loop
    
    try:
        # Verificar conexión
        if not check_connection():
            return {
                'success': False,
                'error': 'Cliente de Telegram no disponible. Intenta nuevamente en unos segundos.'
            }
        
        # Ejecutar la consulta asíncrona en el loop existente
        future = asyncio.run_coroutine_threadsafe(consult_antecedentes_async(dni_number, tipo), loop)
        result = future.result(timeout=35)  # 35 segundos de timeout
        return result
        
    except asyncio.TimeoutError:
        logger.error(f"Timeout consultando {tipo.upper()} DNI {dni_number}")
        return {
            'success': False,
            'error': 'Timeout: No se recibió respuesta en 35 segundos'
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error consultando {tipo.upper()} DNI {dni_number}: {error_msg}")
        
        # Si es error de desconexión, intentar reconectar
        if "disconnected" in error_msg.lower() or "connection" in error_msg.lower():
            logger.info("Error de desconexión detectado, intentando reconectar...")
            try:
                restart_telethon()
                # Esperar un poco para que se reconecte
                time.sleep(3)
                # Intentar la consulta nuevamente
                future = asyncio.run_coroutine_threadsafe(consult_antecedentes_async(dni_number, tipo), loop)
                result = future.result(timeout=35)
                return result
            except Exception as retry_error:
                logger.error(f"Error en reintento: {str(retry_error)}")
        
        return {
            'success': False,
            'error': f'Error en la consulta: {error_msg}'
        }

async def consult_antecedentes_async(dni_number, tipo):
    """Consulta asíncrona de antecedentes (penales, policiales, judiciales)."""
    global client
    
    try:
        max_attempts = 3  # Máximo 3 intentos
        
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Intento {attempt}/{max_attempts} para {tipo.upper()} DNI {dni_number}")
            
            # Determinar comando según tipo
            if tipo == "penales":
                comando = "/antpen"
            elif tipo == "policiales":
                comando = "/antpol"
            elif tipo == "judiciales":
                comando = "/antjud"
            else:
                comando = f"/ant{tipo[:3]}"
            
            # Enviar comando
            await client.send_message(config.TARGET_BOT, f"{comando} {dni_number}")
            logger.info(f"Comando {comando} {dni_number} enviado correctamente (intento {attempt})")
            
            # Esperar un poco antes de revisar mensajes
            await asyncio.sleep(2)
            
            # Obtener mensajes recientes
            messages = await client.get_messages(config.TARGET_BOT, limit=10)
            current_timestamp = time.time()
            new_messages = [msg for msg in messages if msg.date.timestamp() > current_timestamp - 60]
            
            logger.info(f"Revisando {len(new_messages)} mensajes nuevos para {tipo.upper()} DNI {dni_number}...")
            
            for message in new_messages:
                logger.info(f"Mensaje nuevo: {message.text[:100]}...")
                logger.info(f"Texto limpio: {message.text.replace('`', '').replace('*', '').replace('**', '')[:100]}...")
                
                # Buscar mensajes de espera/procesamiento
                if "espera" in message.text.lower() and "segundos" in message.text.lower():
                    wait_match = re.search(r'(\d+)\s*segundos?', message.text)
                    if wait_match:
                        wait_time = int(wait_match.group(1))
                        logger.info(f"Esperando {wait_time} segundos...")
                        await asyncio.sleep(wait_time)
                        continue
                
                # Buscar respuesta específica para antecedentes
                # Limpiar el texto para comparación
                clean_message = message.text.replace('`', '').replace('*', '').replace('**', '')
                if (f"DNI ➾ {dni_number}" in clean_message and 
                    ("CERTIFICADO" in clean_message or "ANTECEDENTES" in clean_message or "OLIMPO_BOT" in clean_message)):
                    
                    logger.info(f"¡Respuesta encontrada para {tipo.upper()} DNI {dni_number}!")
                    logger.info(f"Texto completo: {message.text}")
                    
                    # Encontramos la respuesta
                    text_data = message.text
                    pdf_data = None
                    
                    # Verificar si hay PDF adjunto
                    if message.media and hasattr(message.media, 'document'):
                        logger.info("Descargando PDF...")
                        # Descargar el PDF en memoria
                        pdf_bytes = await client.download_media(message.media, file=BytesIO())
                        pdf_data = pdf_bytes.getvalue()
                        logger.info(f"PDF descargado en memoria: {len(pdf_data)} bytes")
                    else:
                        logger.info("No se detectó PDF adjunto en el mensaje")
                    
                    parsed_data = parse_antecedentes_response(text_data, tipo.upper())
                    logger.info(f"Datos parseados: {parsed_data}")
                    
                    return {
                        'success': True,
                        'text_data': text_data,
                        'pdf_data': pdf_data,
                        'parsed_data': parsed_data
                    }
            
            # Si no se encontró respuesta, esperar antes del siguiente intento
            if attempt < max_attempts:
                logger.warning(f"No se detectó respuesta en intento {attempt}. Esperando 3 segundos...")
                await asyncio.sleep(3)
        
        logger.error(f"Timeout consultando {tipo.upper()} DNI {dni_number}")
        return {
            'success': False,
            'error': 'Timeout: No se recibió respuesta después de 3 intentos'
        }
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error consultando {tipo.upper()} DNI {dni_number}: {error_msg}")
        
        # Si es error de desconexión, intentar reconectar
        if "disconnected" in error_msg.lower() or "connection" in error_msg.lower():
            logger.info("Error de desconexión detectado, intentando reconectar...")
            try:
                restart_telethon()
                # Esperar un poco para que se reconecte
                time.sleep(3)
                # Intentar la consulta nuevamente
                future = asyncio.run_coroutine_threadsafe(consult_antecedentes_async(dni_number, tipo), loop)
                result = future.result(timeout=35)
                return result
            except Exception as retry_error:
                logger.error(f"Error en reintento: {str(retry_error)}")
        
        return {
            'success': False,
            'error': f'Error en la consulta: {error_msg}'
        }

# Crear la aplicación Flask
app = Flask(__name__)

# Inicializar base de datos
init_database()

@app.route('/', methods=['GET'])
def home():
    """Página principal con información del servidor."""
    return jsonify({
        'servicio': 'API Certificados',
        'comandos': {
            'penales': '/antpen?dni=12345678&key=TU_API_KEY',
            'policiales': '/antpol?dni=12345678&key=TU_API_KEY',
            'judiciales': '/antjud?dni=12345678&key=TU_API_KEY'
        },
        'info': '@zGatoO - @WinniePoohOFC - @choco_tete'
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'OK',
        'service': 'Certificados API',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/register-key', methods=['POST'])
def register_key():
    """Endpoint para registrar API Keys desde el panel de administración."""
    try:
        data = request.get_json()
        
        if not data or 'key' not in data:
            return jsonify({
                'success': False,
                'error': 'Datos de API Key requeridos'
            }), 400
        
        api_key = data['key']
        description = data.get('description', 'API Key desde panel')
        expires_at = data.get('expires_at', (datetime.now() + timedelta(hours=1)).isoformat())
        
        if register_api_key(api_key, description, expires_at):
            return jsonify({
                'success': True,
                'message': 'API Key registrada correctamente'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Error registrando API Key'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error interno: {str(e)}'
        }), 500

@app.route('/delete-key', methods=['POST'])
def delete_key():
    """Endpoint para eliminar API Keys desde el panel de administración."""
    try:
        data = request.get_json()
        
        if not data or 'key' not in data:
            return jsonify({
                'success': False,
                'error': 'API Key requerida'
            }), 400
        
        api_key = data['key']
        
        if delete_api_key(api_key):
            return jsonify({
                'success': True,
                'message': 'API Key eliminada correctamente'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Error eliminando API Key'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error interno: {str(e)}'
        }), 500

@app.route('/antpen', methods=['GET'])
def antpen_result():
    """Endpoint para consultar antecedentes penales."""
    # Validar API Key
    api_key = request.args.get('key') or request.headers.get('X-API-Key')
    validation = validate_api_key(api_key)
    
    if not validation['valid']:
        return jsonify({
            'success': False,
            'error': validation['error']
        }), 401
    
    dni = request.args.get('dni')
    
    if not dni:
        return jsonify({
            'success': False,
            'error': 'Parámetro DNI requerido. Use: /antpen?dni=12345678'
        }), 400
    
    # Verificar formato del DNI
    if not dni.isdigit() or len(dni) != 8:
        return jsonify({
            'success': False,
            'error': 'DNI debe ser un número de 8 dígitos'
        }), 400
    
    # Ejecutar consulta síncrona
    result = consult_antecedentes_sync(dni, 'penales')
    
    if result['success']:
        # Si hay PDF, mostrar página HTML con datos y descarga automática
        if result['pdf_data']:
            # Crear respuesta JSON con PDF en base64 para descarga automática
            import base64
            pdf_base64 = base64.b64encode(result['pdf_data']).decode('utf-8')
            
            json_data = {
                'success': True,
                'dni': dni,
                'tipo': 'ANTECEDENTES_PENALES',
                'timestamp': datetime.now().isoformat(),
                'data': result['parsed_data'],
                'pdf_base64': pdf_base64,
                'pdf_filename': f"antecedentes_penales_{dni}.pdf"
            }
            
            # Crear respuesta HTML completa con descarga automática
            response_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Antecedentes Penales - DNI {dni}</title>
                <meta charset="utf-8">
            </head>
            <body>
                <pre id="json-data">{json.dumps(json_data, indent=2, ensure_ascii=False)}</pre>
                <script>
                    // Descargar PDF automáticamente cuando la página cargue
                    window.onload = function() {{
                        const pdfData = '{pdf_base64}';
                        const pdfBlob = new Blob([Uint8Array.from(atob(pdfData), c => c.charCodeAt(0))], {{type: 'application/pdf'}});
                        const url = URL.createObjectURL(pdfBlob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'antecedentes_penales_{dni}.pdf';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                    }};
                </script>
            </body>
            </html>
            """
            
            return response_html
        else:
            # Si no hay PDF, devolver solo JSON
            response = {
                'success': True,
                'dni': dni,
                'tipo': 'ANTECEDENTES_PENALES',
                'timestamp': datetime.now().isoformat(),
                'data': result['parsed_data']
            }
            return jsonify(response)
    else:
        return jsonify({
            'success': False,
            'error': result['error']
        }), 500

@app.route('/antpol', methods=['GET'])
def antpol_result():
    """Endpoint para consultar antecedentes policiales."""
    # Validar API Key
    api_key = request.args.get('key') or request.headers.get('X-API-Key')
    validation = validate_api_key(api_key)
    
    if not validation['valid']:
        return jsonify({
            'success': False,
            'error': validation['error']
        }), 401
    
    dni = request.args.get('dni')
    
    if not dni:
        return jsonify({
            'success': False,
            'error': 'Parámetro DNI requerido. Use: /antpol?dni=12345678'
        }), 400
    
    # Verificar formato del DNI
    if not dni.isdigit() or len(dni) != 8:
        return jsonify({
            'success': False,
            'error': 'DNI debe ser un número de 8 dígitos'
        }), 400
    
    # Ejecutar consulta síncrona
    result = consult_antecedentes_sync(dni, 'policiales')
    
    if result['success']:
        # Si hay PDF, mostrar página HTML con datos y descarga automática
        if result['pdf_data']:
            # Crear respuesta JSON con PDF en base64 para descarga automática
            import base64
            pdf_base64 = base64.b64encode(result['pdf_data']).decode('utf-8')
            
            json_data = {
                'success': True,
                'dni': dni,
                'tipo': 'ANTECEDENTES_POLICIALES',
                'timestamp': datetime.now().isoformat(),
                'data': result['parsed_data'],
                'pdf_base64': pdf_base64,
                'pdf_filename': f"antecedentes_policiales_{dni}.pdf"
            }
            
            # Crear respuesta HTML completa con descarga automática
            response_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Antecedentes Policiales - DNI {dni}</title>
                <meta charset="utf-8">
            </head>
            <body>
                <pre id="json-data">{json.dumps(json_data, indent=2, ensure_ascii=False)}</pre>
                <script>
                    // Descargar PDF automáticamente cuando la página cargue
                    window.onload = function() {{
                        const pdfData = '{pdf_base64}';
                        const pdfBlob = new Blob([Uint8Array.from(atob(pdfData), c => c.charCodeAt(0))], {{type: 'application/pdf'}});
                        const url = URL.createObjectURL(pdfBlob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'antecedentes_policiales_{dni}.pdf';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                    }};
                </script>
            </body>
            </html>
            """
            
            return response_html
        else:
            # Si no hay PDF, devolver solo JSON
            response = {
                'success': True,
                'dni': dni,
                'tipo': 'ANTECEDENTES_POLICIALES',
                'timestamp': datetime.now().isoformat(),
                'data': result['parsed_data']
            }
            return jsonify(response)
    else:
        return jsonify({
            'success': False,
            'error': result['error']
        }), 500

@app.route('/antjud', methods=['GET'])
def antjud_result():
    """Endpoint para consultar antecedentes judiciales."""
    # Validar API Key
    api_key = request.args.get('key') or request.headers.get('X-API-Key')
    validation = validate_api_key(api_key)
    
    if not validation['valid']:
        return jsonify({
            'success': False,
            'error': validation['error']
        }), 401
    
    dni = request.args.get('dni')
    
    if not dni:
        return jsonify({
            'success': False,
            'error': 'Parámetro DNI requerido. Use: /antjud?dni=12345678'
        }), 400
    
    # Verificar formato del DNI
    if not dni.isdigit() or len(dni) != 8:
        return jsonify({
            'success': False,
            'error': 'DNI debe ser un número de 8 dígitos'
        }), 400
    
    # Ejecutar consulta síncrona
    result = consult_antecedentes_sync(dni, 'judiciales')
    
    if result['success']:
        # Si hay PDF, mostrar página HTML con datos y descarga automática
        if result['pdf_data']:
            # Crear respuesta JSON con PDF en base64 para descarga automática
            import base64
            pdf_base64 = base64.b64encode(result['pdf_data']).decode('utf-8')
            
            json_data = {
                'success': True,
                'dni': dni,
                'tipo': 'ANTECEDENTES_JUDICIALES',
                'timestamp': datetime.now().isoformat(),
                'data': result['parsed_data'],
                'pdf_base64': pdf_base64,
                'pdf_filename': f"antecedentes_judiciales_{dni}.pdf"
            }
            
            # Crear respuesta HTML completa con descarga automática
            response_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Antecedentes Judiciales - DNI {dni}</title>
                <meta charset="utf-8">
            </head>
            <body>
                <pre id="json-data">{json.dumps(json_data, indent=2, ensure_ascii=False)}</pre>
                <script>
                    // Descargar PDF automáticamente cuando la página cargue
                    window.onload = function() {{
                        const pdfData = '{pdf_base64}';
                        const pdfBlob = new Blob([Uint8Array.from(atob(pdfData), c => c.charCodeAt(0))], {{type: 'application/pdf'}});
                        const url = URL.createObjectURL(pdfBlob);
                        const a = document.createElement('a');
                        a.href = url;
                        a.download = 'antecedentes_judiciales_{dni}.pdf';
                        document.body.appendChild(a);
                        a.click();
                        document.body.removeChild(a);
                        URL.revokeObjectURL(url);
                    }};
                </script>
            </body>
            </html>
            """
            
            return response_html
        else:
            # Si no hay PDF, devolver solo JSON
            response = {
                'success': True,
                'dni': dni,
                'tipo': 'ANTECEDENTES_JUDICIALES',
                'timestamp': datetime.now().isoformat(),
                'data': result['parsed_data']
            }
            return jsonify(response)
    else:
        return jsonify({
            'success': False,
            'error': result['error']
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint de salud de la API."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'WolfData Certificados API'
    })


def restart_telethon():
    """Reinicia el cliente de Telethon."""
    global client, loop
    try:
        if client:
            client.disconnect()
        if loop:
            loop.close()
        
        # Reinicializar en un nuevo hilo
        init_telethon_thread()
        logger.info("Cliente de Telethon reiniciado")
    except Exception as e:
        logger.error(f"Error reiniciando Telethon: {str(e)}")

def restart_telethon():
    """Reinicia la conexión de Telethon."""
    global client, loop
    
    try:
        if client:
            logger.info("Cerrando cliente anterior...")
            try:
                # Esperar a que se desconecte
                future = client.disconnect()
                if future and not future.done():
                    # Esperar máximo 5 segundos
                    import concurrent.futures
                    try:
                        future.result(timeout=5)
                    except concurrent.futures.TimeoutError:
                        logger.warning("Timeout cerrando cliente anterior")
            except Exception as e:
                logger.warning(f"Error cerrando cliente anterior: {e}")
            time.sleep(2)
        
        # Crear nuevo cliente
        client = TelegramClient(
            'telethon_session',
            config.API_ID,
            config.API_HASH
        )
        
        # Iniciar en el loop existente
        if loop and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(client.start(), loop)
            future.result(timeout=30)
            logger.info("Cliente de Telethon reiniciado correctamente")
        else:
            logger.error("No hay loop de asyncio disponible para reiniciar")
            
    except Exception as e:
        logger.error(f"Error reiniciando Telethon: {str(e)}")

def init_telethon_thread():
    """Inicializa Telethon en un hilo separado."""
    global client, loop
    
    def run_telethon():
        global client, loop
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            client = TelegramClient(
                'telethon_session',
                config.API_ID,
                config.API_HASH
            )
            
            # Iniciar el cliente de forma asíncrona
            async def start_client():
                await client.start()
                logger.info("Cliente de Telethon iniciado correctamente")
            
            loop.run_until_complete(start_client())
            
            # Mantener el loop corriendo
            loop.run_forever()
            
        except Exception as e:
            logger.error(f"Error inicializando Telethon: {str(e)}")
    
    # Iniciar en hilo separado
    thread = threading.Thread(target=run_telethon, daemon=True)
    thread.start()
    
    # Esperar un poco para que se inicialice
    time.sleep(3)

def main():
    """Función principal."""
    # Inicializar Telethon en hilo separado
    init_telethon_thread()
    
    # Iniciar Flask
    port = int(os.getenv('PORT', 8080))
    logger.info(f"Iniciando API en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()
