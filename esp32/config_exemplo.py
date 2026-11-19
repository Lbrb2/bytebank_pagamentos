"""
config_exemplo.py - Modelo de configuração do terminal

Copie este arquivo para config.py e preencha com os dados reais.
O config.py está no .gitignore: senhas não vão para o repositório público.
"""

# Rede Wi-Fi (o ESP32 só conecta em redes de 2,4 GHz)
WIFI_SSID = "digite sua rede"
WIFI_SENHA = "digite sua senha"

# Endereço do backend (usado a partir da Entrega 2)
API_URL = "http://192.168.0.10:8000"

# Identificação deste terminal no sistema
TERMINAL_ID = "POS-01"

# Pino do botão: 32 = botão azul no protoboard; 0 = botão BOOT da própria
# placa (serve para testar sem ligar fio nenhum)
PINO_BOTAO = 32

# Teclado de membrana 4x4 (opcional). Ligações no README ("Mapa de pinos"):
# linhas nos GPIO 13, 14, 25, 33 e colunas nos GPIO 21, 22, 15, 32.
# Com o teclado, use o botão BOOT (PINO_BOTAO = 0): o 32 vira coluna.
TECLADO = False

# PIN do modo operador (de 4 a 8 dígitos). TROQUE antes de usar.
# Para abrir o modo: carrinho vazio e segurar o botão por 5 segundos.
# Apague a linha para desligar o modo operador.
PIN_OPERADOR = "2580"
