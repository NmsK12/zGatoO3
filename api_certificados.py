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

def consult_antecedentes_sync(dni_number, tipo):
    """Consulta antecedentes usando Telethon de forma síncrona."""
    global client, loop
    
    try:
        # Verificar que el cliente esté disponible
        if not client:
            return {
                'success': False,
                'error': 'Cliente de Telegram no inicializado'
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
        logger.error(f"Error consultando {tipo.upper()} DNI {dni_number}: {str(e)}")
        return {
            'success': False,
            'error': f'Error en la consulta: {str(e)}'
        }

async def consult_antecedentes_async(dni_number, tipo):
    """Consulta asíncrona de antecedentes (penales, policiales, judiciales)."""
    global client
    
    try:
        max_attempts = 3  # Máximo 3 intentos
        
        for attempt in range(1, max_attempts + 1):
            logger.info(f"Intento {attempt}/{max_attempts} para {tipo.upper()} DNI {dni_number}")
            
            # Determinar comando según tipo
            comando = f"/ant{tipo[:3]}"  # antpen, antpol, antjud
            
            # Enviar comando
            await client.send_message(config.TARGET_BOT, f"{comando} {dni_number}")
            logger.info(f"Comando {comando} enviado correctamente (intento {attempt})")
            
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
        logger.error(f"Error consultando {tipo.upper()} DNI {dni_number}: {str(e)}")
        return {
            'success': False,
            'error': f'Error en la consulta: {str(e)}'
        }

# Crear la aplicación Flask
app = Flask(__name__)

@app.route('/antpen', methods=['GET'])
def antpen_result():
    """Endpoint para consultar antecedentes penales."""
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

@app.route('/', methods=['GET'])
def home():
    """Página de inicio de la API."""
    return jsonify({
        'service': 'WolfData Certificados API',
        'version': '1.0.0',
        'endpoints': {
            'antecedentes_penales': '/antpen?dni=12345678',
            'antecedentes_policiales': '/antpol?dni=12345678',
            'antecedentes_judiciales': '/antjud?dni=12345678',
            'health': '/health'
        },
        'description': 'API especializada para consultas de antecedentes (penales, policiales, judiciales) con PDFs'
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
            
            loop.run_until_complete(client.start())
            logger.info("Cliente de Telethon iniciado correctamente")
            
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
