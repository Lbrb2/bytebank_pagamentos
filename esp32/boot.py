"""
boot.py - Inicialização do terminal ByteBank Pay (ESP32 / MicroPython)

O ESP32 executa este arquivo automaticamente ao ligar, ANTES do main.py.
Responsabilidade única: colocar o terminal na rede Wi-Fi.

Se a rede não estiver disponível, o boot NÃO trava: o terminal segue para o
main.py em modo offline, e as vendas vão para a fila de contingência até a
conexão voltar (Entrega 2).

As credenciais ficam em config.py, que NÃO vai para o GitHub (está no
.gitignore). Para criar o seu, copie config_exemplo.py para config.py e
preencha os dados da rede.
"""

import gc
import time

import wifi

FUSO_HORARIO_HORAS = -3   # horário de Brasília, usado só para exibir a hora


CAMPOS_OBRIGATORIOS = ("WIFI_SSID", "WIFI_SENHA")
VALOR_DE_EXEMPLO = "nome_da_rede"
SENHA_DE_EXEMPLO = "senha_da_rede"


def carregar_configuracao():
    """Importa o config.py e confere se ele tem o necessário para conectar."""
    try:
        import config
    except ImportError:
        print("[BOOT] config.py não encontrado.")
        print("[BOOT] Copie config_exemplo.py para config.py e preencha a rede Wi-Fi.")
        return None

    faltando = [campo for campo in CAMPOS_OBRIGATORIOS if not hasattr(config, campo)]
    if faltando:
        print("[BOOT] config.py incompleto. Falta: {}".format(", ".join(faltando)))
        return None
    if config.WIFI_SSID == VALOR_DE_EXEMPLO or config.WIFI_SENHA == SENHA_DE_EXEMPLO:
        print("[BOOT] config.py ainda tem valores de exemplo. Preencha WIFI_SSID e WIFI_SENHA.")
        return None
    return config


def sincronizar_relogio():
    """Acerta o relógio pela internet (NTP).

    As vendas feitas offline precisam de data e hora corretas para entrar
    na ordem certa quando forem enviadas. O relógio interno guarda UTC.
    """
    try:
        import ntptime
        ntptime.settime()
        ano, mes, dia, hora, minuto = time.localtime(
            time.time() + FUSO_HORARIO_HORAS * 3600)[:5]
        print("[BOOT] Relógio sincronizado: {:02d}/{:02d}/{} {:02d}:{:02d}".format(
            dia, mes, ano, hora, minuto))
        return True
    except Exception as erro:   # sem internet, DNS ou servidor NTP fora
        print("[BOOT] Não foi possível sincronizar o relógio: {}".format(erro))
        return False


def iniciar():
    print()
    print("=" * 40)
    print("  ByteBank Pay - Terminal POS")
    print("=" * 40)

    config = carregar_configuracao()
    online = False

    if config is not None:
        online = wifi.conectar(config.WIFI_SSID, config.WIFI_SENHA)

    if online:
        info = wifi.informacoes() or {"ip": "?", "gateway": "?", "rssi": None}  # rede pode cair logo após conectar
        sinal = "{} dBm".format(info["rssi"]) if info["rssi"] is not None else "desconhecido"
        print("[BOOT] Rede: IP {} | gateway {} | sinal {}".format(
            info["ip"], info["gateway"], sinal))
        sincronizar_relogio()
    else:
        print("[BOOT] Iniciando em MODO OFFLINE.")

    gc.collect()
    print("[BOOT] Memória livre: {} bytes".format(gc.mem_free()))
    print("[BOOT] Inicialização concluída.")
    return online


# Qualquer erro inesperado é exibido, mas não impede o main.py de rodar
try:
    ONLINE = iniciar()
except Exception as erro:
    ONLINE = False
    print("[BOOT] Erro inesperado na inicialização: {}".format(erro))
