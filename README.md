# WolfData Certificados API

Servidor especializado para consultas de antecedentes (penales, policiales, judiciales).

## Endpoints

- `GET /antpen?dni=12345678` - Antecedentes penales
- `GET /antpol?dni=12345678` - Antecedentes policiales
- `GET /antjud?dni=12345678` - Antecedentes judiciales
- `GET /health` - Estado de salud del servicio
- `GET /` - Información del servicio

## Características

- Consultas de antecedentes penales, policiales y judiciales
- Descarga automática de PDFs
- Datos JSON con información parseada
- Sistema de cola inteligente
- Manejo de errores y reintentos
- Sin sistema de tokens (ilimitado)

## Instalación

```bash
pip install -r requirements.txt
python api_certificados.py
```

## Variables de Entorno

- `API_ID` - ID de la API de Telegram
- `API_HASH` - Hash de la API de Telegram
- `TARGET_BOT` - Bot objetivo (@OlimpoDataBot)
- `PORT` - Puerto del servidor (default: 8080)
