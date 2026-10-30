"""
rc522.py - Driver mínimo do leitor RFID RC522 (MicroPython / ESP32)

Só o necessário para ler o UID (número de série) dos cartões e chaveiros
MIFARE de 13,56 MHz que vêm com o kit. Baseado no driver mfrc522 mais usado
em MicroPython (wendlers), mas escrito para o MicroPython atual: usa o SPI
por hardware do ESP32 e não depende de nenhum arquivo externo.

Ligação (o RC522 funciona com 3,3 V; NÃO ligue no 5 V):

    RC522   ESP32
    SDA  -> GPIO 27   (seleção do chip, "CS")
    SCK  -> GPIO 18
    MOSI -> GPIO 23
    MISO -> GPIO 19
    IRQ  -> (não liga)
    GND  -> GND
    RST  -> GPIO 26
    3.3V -> 3V3

Uso:
    leitor = RC522(sck=18, mosi=23, miso=19, rst=26, cs=27)
    estado, _ = leitor.request(leitor.REQIDL)
    if estado == leitor.OK:
        estado, uid = leitor.anticoll()     # uid: 4 bytes + 1 de verificação
"""

import time
from machine import Pin

try:
    from machine import SPI
except ImportError:   # pragma: no cover
    SPI = None

# Registradores do RC522 (datasheet, seção 9)
_COMANDO = 0x01
_IRQ_HABILITA = 0x02
_IRQ = 0x04
_ERRO = 0x06
_FIFO_DADOS = 0x09
_FIFO_NIVEL = 0x0A
_CONTROLE = 0x0C
_BITS = 0x0D
_MODO = 0x11
_TX_CONTROLE = 0x14
_TX_ASK = 0x15
_TIMER_MODO = 0x2A
_TIMER_PRESCALER = 0x2B
_TIMER_RELOAD_ALTO = 0x2C
_TIMER_RELOAD_BAIXO = 0x2D
_VERSAO = 0x37

_CMD_PARADO = 0x00
_CMD_TRANSCEIVE = 0x0C
_CMD_RESET = 0x0F


class RC522:
    OK = 0
    SEM_CARTAO = 1
    ERRO = 2
    REQIDL = 0x26     # procura cartões que ainda não foram lidos
    REQALL = 0x52     # procura todos os cartões no campo

    def __init__(self, sck, mosi, miso, rst, cs, baudrate=1000000, spi_id=2):
        self._rst = Pin(rst, Pin.OUT, value=0)
        self._cs = Pin(cs, Pin.OUT, value=1)
        self._spi = SPI(spi_id, baudrate=baudrate, polarity=0, phase=0,
                        sck=Pin(sck), mosi=Pin(mosi), miso=Pin(miso))
        self._rst.value(1)
        time.sleep_ms(50)
        self.iniciar()

    # ---------------- Acesso aos registradores ----------------

    def _escrever(self, registrador, valor):
        self._cs.value(0)
        self._spi.write(bytes(((registrador << 1) & 0x7E, valor)))
        self._cs.value(1)

    def _ler(self, registrador):
        self._cs.value(0)
        self._spi.write(bytes((((registrador << 1) & 0x7E) | 0x80,)))
        valor = self._spi.read(1)
        self._cs.value(1)
        return valor[0]

    def _ligar_bits(self, registrador, mascara):
        self._escrever(registrador, self._ler(registrador) | mascara)

    def _desligar_bits(self, registrador, mascara):
        self._escrever(registrador, self._ler(registrador) & (~mascara & 0xFF))

    # ---------------- Inicialização ----------------

    def versao(self):
        """0x91 ou 0x92 nos chips originais; 0x88, 0x12 ou 0xB2 em clones.
        0x00 ou 0xFF quase sempre é fio errado ou solto."""
        return self._ler(_VERSAO)

    def iniciar(self):
        self._escrever(_COMANDO, _CMD_RESET)
        time.sleep_ms(50)
        self._escrever(_TIMER_MODO, 0x8D)        # tempo limite de ~25 ms por comando
        self._escrever(_TIMER_PRESCALER, 0x3E)
        self._escrever(_TIMER_RELOAD_BAIXO, 30)
        self._escrever(_TIMER_RELOAD_ALTO, 0)
        self._escrever(_TX_ASK, 0x40)            # modulação 100% ASK
        self._escrever(_MODO, 0x3D)              # CRC padrão do MIFARE
        self._ligar_bits(_TX_CONTROLE, 0x03)     # liga a antena

    # ---------------- Comunicação com o cartão ----------------

    def _transceive(self, dados):
        """Envia bytes ao cartão e devolve (estado, bytes_recebidos, bits)."""
        recebidos = []
        bits = 0
        self._escrever(_IRQ_HABILITA, 0x77 | 0x80)
        self._desligar_bits(_IRQ, 0x80)
        self._ligar_bits(_FIFO_NIVEL, 0x80)      # esvazia a FIFO
        self._escrever(_COMANDO, _CMD_PARADO)
        for byte in dados:
            self._escrever(_FIFO_DADOS, byte)
        self._escrever(_COMANDO, _CMD_TRANSCEIVE)
        self._ligar_bits(_BITS, 0x80)            # começa a transmitir

        tentativas = 2000
        while True:
            irq = self._ler(_IRQ)
            tentativas -= 1
            # 0x30: recebeu resposta ou terminou; 0x01: o temporizador estourou
            if (irq & 0x30) or (irq & 0x01) or tentativas == 0:
                break
        self._desligar_bits(_BITS, 0x80)

        if tentativas == 0 or (self._ler(_ERRO) & 0x1B):
            return self.ERRO, recebidos, bits
        if irq & 0x01:
            return self.SEM_CARTAO, recebidos, bits

        quantidade = self._ler(_FIFO_NIVEL)
        ultimos_bits = self._ler(_CONTROLE) & 0x07
        bits = (quantidade - 1) * 8 + ultimos_bits if ultimos_bits else quantidade * 8
        quantidade = min(max(quantidade, 1), 16)
        for _ in range(quantidade):
            recebidos.append(self._ler(_FIFO_DADOS))
        return self.OK, recebidos, bits

    def request(self, modo=REQIDL):
        """Pergunta se há cartão no campo. Devolve (estado, bits)."""
        self._escrever(_BITS, 0x07)              # REQA tem só 7 bits
        estado, _, bits = self._transceive([modo])
        if estado != self.OK or bits != 0x10:
            estado = self.ERRO if estado == self.OK else estado
        return estado, bits

    def anticoll(self):
        """Lê o UID. Devolve (estado, [4 bytes do UID + 1 byte de verificação])."""
        self._escrever(_BITS, 0x00)
        estado, recebidos, _ = self._transceive([0x93, 0x20])
        if estado == self.OK:
            if len(recebidos) == 5:
                verificacao = 0
                for byte in recebidos[:4]:
                    verificacao ^= byte
                if verificacao != recebidos[4]:
                    estado = self.ERRO
            else:
                estado = self.ERRO
        return estado, recebidos
