"""
wifi.py - Conexão Wi-Fi do terminal ByteBank Pay (ESP32 / MicroPython)

Usado pelo boot.py na inicialização e, a partir da Entrega 2, pelo main.py
para reconectar quando a rede cair no meio do funcionamento.

Regra de ouro deste módulo: NUNCA travar a placa. Toda espera tem tempo
limite. Um laço infinito no boot.py deixaria o ESP32 preso antes do main.py
e dificultaria até regravar o código pela USB.
"""

import network
import time
from machine import Pin

TEMPO_LIMITE_MS = 15000      # espera máxima por tentativa
TENTATIVAS = 3
PAUSA_ENTRE_TENTATIVAS_MS = 1000
PINO_LED_STATUS = 16         # LED da placa usada pela equipe (testado)

_wlan = network.WLAN(network.STA_IF)
_led = Pin(PINO_LED_STATUS, Pin.OUT)

# Códigos de status variam entre versões do firmware; por isso são lidos
# com getattr, e um nome que não existir é simplesmente ignorado.
_MENSAGENS_DE_STATUS = {
    "STAT_IDLE": "parado",
    "STAT_CONNECTING": "conectando",
    "STAT_WRONG_PASSWORD": "senha incorreta",
    "STAT_NO_AP_FOUND": "rede não encontrada",
    "STAT_ASSOC_FAIL": "falha de associação com o roteador",
    "STAT_HANDSHAKE_TIMEOUT": "tempo esgotado na autenticação",
    "STAT_BEACON_TIMEOUT": "sinal perdido",
    "STAT_CONNECT_FAIL": "falha de conexão",
    "STAT_GOT_IP": "conectado",
}

# Senha errada não melhora tentando de novo: desiste na hora
_STATUS_SEM_SOLUCAO = ("STAT_WRONG_PASSWORD",)

# Falhas que encerram a tentativa atual. Não adianta esperar o tempo
# limite: o ESP32 já desistiu daquela tentativa, e é melhor tentar de novo.
_STATUS_DE_FALHA = ("STAT_NO_AP_FOUND", "STAT_ASSOC_FAIL", "STAT_HANDSHAKE_TIMEOUT",
                    "STAT_BEACON_TIMEOUT", "STAT_CONNECT_FAIL")


def _codigo(nome):
    return getattr(network, nome, None)


def descrever_status(codigo):
    for nome, mensagem in _MENSAGENS_DE_STATUS.items():
        if _codigo(nome) == codigo:
            return mensagem
    return "status {}".format(codigo)


def _falha_sem_solucao(codigo):
    return any(_codigo(nome) == codigo for nome in _STATUS_SEM_SOLUCAO)


def _falha_da_tentativa(codigo):
    return any(_codigo(nome) == codigo for nome in _STATUS_DE_FALHA)


def conectado():
    return _wlan.isconnected()


def conectar(ssid, senha, tentativas=TENTATIVAS, tempo_limite_ms=TEMPO_LIMITE_MS):
    """Conecta à rede. Devolve True se conseguiu e False caso contrário.

    Faz até `tentativas` tentativas de até `tempo_limite_ms` cada, piscando
    o LED de status enquanto espera. Com sucesso, o LED fica aceso.
    """
    _wlan.active(True)
    if _wlan.isconnected():
        _led.on()
        return True

    for tentativa in range(1, tentativas + 1):
        print("[Wi-Fi] Tentativa {}/{}: conectando a '{}'...".format(
            tentativa, tentativas, ssid))
        try:
            _wlan.connect(ssid, senha)
        except OSError as erro:
            # O ESP32 lança "Wifi Internal Error" se uma conexão anterior
            # ainda estiver em andamento. Desconectar e tentar de novo resolve.
            print("[Wi-Fi] Erro ao iniciar a conexão: {}".format(erro))
            _desconectar()
            time.sleep_ms(PAUSA_ENTRE_TENTATIVAS_MS)
            continue

        inicio = time.ticks_ms()
        while not _wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), inicio) > tempo_limite_ms:
                print("[Wi-Fi] Tempo esgotado ({} s): {}.".format(
                    tempo_limite_ms // 1000, descrever_status(_wlan.status())))
                break
            status = _wlan.status()
            if _falha_sem_solucao(status):
                print("[Wi-Fi] {}. Confira o config.py.".format(
                    descrever_status(status).capitalize()))
                _desconectar()
                _led.off()
                return False
            if _falha_da_tentativa(status):
                print("[Wi-Fi] Falhou: {}.".format(descrever_status(status)))
                break
            _led.value(not _led.value())   # pisca enquanto conecta
            time.sleep_ms(250)

        if _wlan.isconnected():
            _led.on()
            print("[Wi-Fi] Conectado. IP: {}".format(_wlan.ifconfig()[0]))
            return True

        _desconectar()
        time.sleep_ms(PAUSA_ENTRE_TENTATIVAS_MS)

    _led.off()
    print("[Wi-Fi] Não foi possível conectar após {} tentativa(s).".format(tentativas))
    return False


def _desconectar():
    try:
        _wlan.disconnect()
    except OSError:
        pass


def informacoes():
    """Dados da conexão atual, para log e telemetria. None se desconectado."""
    if not _wlan.isconnected():
        return None
    ip, mascara, gateway, dns = _wlan.ifconfig()
    try:
        rssi = _wlan.status("rssi")   # intensidade do sinal, em dBm
    except (OSError, ValueError, TypeError):
        rssi = None
    return {"ip": ip, "mascara": mascara, "gateway": gateway, "dns": dns, "rssi": rssi}
