"""
hardware.py - Adaptadores dos componentes do terminal (MicroPython)

Cada classe esconde os detalhes de um componente. O resto do programa só
chama métodos simples: display.mostrar(linhas), botao.ler(), rfid.ler(),
sinal.aprovado(). Se um componente mudar, só a classe dele muda.

Todas são tolerantes a falhas: se o driver não estiver na placa ou o
componente não responder, a classe avisa no console e o terminal continua
funcionando sem aquele componente, em vez de travar.

Componentes da bancada (confirmados em 29/10):
  * ESP32 com display OLED 0,96" (SSD1306, 128x64) soldado na placa
  * leitor RFID RC522 (placa azul HW-126), driver próprio em rc522.py
  * um botão de pressão (12x12 mm)
Para conferir as ligações, rode o diagnostico.py.
"""

import time
from machine import Pin

# ---------------- Pinos ----------------

# O display vem ligado por dentro da placa. Nas placas com OLED + bateria
# 18650 ele usa SDA=5 e SCL=4; os outros pares são tentados por garantia.
OLED_CANDIDATOS = ((5, 4), (4, 5), (21, 22))     # (SDA, SCL)
OLED_ENDERECOS = (0x3C, 0x3D)

RFID_SCK, RFID_MOSI, RFID_MISO, RFID_RST, RFID_SDA = 18, 23, 19, 26, 27

PINO_BOTAO = 32          # um terminal no GPIO 32, o outro no GND (em diagonal)
                         # 0 = botão BOOT da própria placa (config.py: PINO_BOTAO = 0)

PINO_BUZZER = 17         # opcionais: se não estiverem ligados, nada acontece
PINO_LED_APROVADO = 2

# Eventos do botão (o terminal.py recebe estes textos)
TOQUE = "toque"                  # apertar e soltar rápido
SEGURAR = "segurar"              # segurar ~1 s e soltar
SEGURAR_LONGO = "segurar_longo"  # segurar 5 s (modo operador / limpar carrinho)


# ======================================================================
# Display OLED SSD1306 (I2C, 128x64)
# ======================================================================

class Display:
    """Mostra até 8 linhas de 16 caracteres no OLED.

    O conteúdo também é impresso no console (Shell do Thonny), o que ajuda
    a depurar e permite usar o terminal mesmo sem o display.
    """

    def __init__(self, candidatos=OLED_CANDIDATOS, espelhar_no_console=True):
        self._oled = None
        self._espelhar = espelhar_no_console
        self._ultimas = None
        try:
            from machine import SoftI2C
            import ssd1306
        except ImportError:
            print("[DISPLAY] Driver ssd1306.py não está na placa. Usando só o console.")
            print("[DISPLAY] Para instalar (com Wi-Fi): import mip; mip.install('ssd1306')")
            return
        for sda, scl in candidatos:
            try:
                i2c = SoftI2C(sda=Pin(sda), scl=Pin(scl), freq=400000)
                encontrados = i2c.scan()
                for endereco in OLED_ENDERECOS:
                    if endereco in encontrados:
                        self._oled = ssd1306.SSD1306_I2C(128, 64, i2c, addr=endereco)
                        print("[DISPLAY] OLED pronto (SDA={} SCL={}).".format(sda, scl))
                        return
            except OSError:
                pass
        print("[DISPLAY] Nenhum OLED encontrado. Usando só o console. Rode o diagnostico.py.")

    def disponivel(self):
        return self._oled is not None

    def mostrar(self, linhas):
        if linhas == self._ultimas:
            return            # nada mudou: evita piscar e economiza tempo
        self._ultimas = list(linhas)

        if self._espelhar:
            print("+----------------+")
            for texto in linhas:
                print("|{:<16}|".format(texto))
            print("+----------------+")

        if self._oled is not None:
            try:
                self._oled.fill(0)
                for numero, texto in enumerate(linhas[:8]):
                    self._oled.text(texto, 0, numero * 8)
                self._oled.show()
            except OSError as erro:
                # Fio solto durante o uso: avisa, mas não derruba o terminal
                print("[DISPLAY] Falha ao atualizar o OLED: {}".format(erro))


# ======================================================================
# Botão
# ======================================================================

class Botao:
    """Transforma um único botão em três comandos, pelo tempo apertado:

        toque          (menos de 0,8 s)  -> TOQUE: próximo item
        segurar        (0,8 s a 5 s)     -> SEGURAR: escolhe o item
        segurar 5 s                      -> SEGURAR_LONGO

    Ao completar 0,8 s apertado, chama ao_segurar() (um bipe), para a
    pessoa saber que já pode soltar. O evento SEGURAR sai quando ela solta;
    o SEGURAR_LONGO sai assim que completa 5 s, sem precisar soltar.

    O botão liga o pino ao GND; o resistor de pull-up interno mantém o pino
    em 1 quando solto. O contato "trepida" ao ser pressionado (bouncing),
    por isso cada mudança só vale depois de `debounce_ms` estável.
    """

    def __init__(self, pino=PINO_BOTAO, debounce_ms=30, segurar_ms=800, longo_ms=5000,
                 ao_segurar=None):
        self._pino = Pin(pino, Pin.IN, Pin.PULL_UP)
        self._debounce = debounce_ms
        self._segurar = segurar_ms
        self._longo = longo_ms
        self._ao_segurar = ao_segurar
        self._lido = False          # última leitura bruta (True = apertado)
        self._mudou_em = time.ticks_ms()
        self._estavel = False       # estado aceito depois do debounce
        self._apertado_em = 0
        self._avisou = False
        self._longo_enviado = False

    def ler(self):
        """Devolve TOQUE, SEGURAR, SEGURAR_LONGO ou None (nada novo)."""
        agora = time.ticks_ms()
        apertado = self._pino.value() == 0

        if apertado != self._lido:
            self._lido = apertado
            self._mudou_em = agora
        elif apertado != self._estavel and time.ticks_diff(agora, self._mudou_em) >= self._debounce:
            self._estavel = apertado
            if apertado:
                self._apertado_em = agora
                self._avisou = False
                self._longo_enviado = False
                return None
            # Soltou
            if self._longo_enviado:
                return None
            duracao = time.ticks_diff(agora, self._apertado_em)
            return SEGURAR if duracao >= self._segurar else TOQUE

        if self._estavel:                       # continua apertado
            duracao = time.ticks_diff(agora, self._apertado_em)
            if not self._avisou and duracao >= self._segurar:
                self._avisou = True
                if self._ao_segurar is not None:
                    self._ao_segurar()
            if not self._longo_enviado and duracao >= self._longo:
                self._longo_enviado = True
                return SEGURAR_LONGO
        return None


# ======================================================================
# Leitor RFID RC522 (SPI)
# ======================================================================

class LeitorRFID:
    """Lê o UID (identificador) dos cartões RFID.

    Usa o rc522.py do repositório. Se ele não estiver na placa, tenta o
    mfrc522.py mais comum na internet (mesmos métodos request/anticoll).

    O mesmo cartão encostado continua sendo lido várias vezes por segundo.
    Para não cobrar duas vezes, o mesmo UID é ignorado por alguns segundos.
    """

    def __init__(self, sck=RFID_SCK, mosi=RFID_MOSI, miso=RFID_MISO, rst=RFID_RST, sda=RFID_SDA,
                 intervalo_mesmo_cartao_ms=3000):
        self._leitor = None
        self._ultimo_uid = None
        self._ultimo_em = 0
        self._intervalo = intervalo_mesmo_cartao_ms
        try:
            try:
                from rc522 import RC522
                leitor = RC522(sck=sck, mosi=mosi, miso=miso, rst=rst, cs=sda)
            except ImportError:
                from mfrc522 import MFRC522
                leitor = MFRC522(sck, mosi, miso, rst, sda)
        except ImportError:
            print("[RFID] Driver rc522.py não está na placa. Leitor desativado.")
            return
        except Exception as erro:
            print("[RFID] Falha ao iniciar o leitor ({}). Confira as ligações SPI.".format(erro))
            return
        versao = leitor.versao() if hasattr(leitor, "versao") else None
        if versao in (0x00, 0xFF):
            print("[RFID] O leitor não respondeu (versão {}). Confira os fios e o 3V3.".format(
                hex(versao)))
            return
        self._leitor = leitor
        print("[RFID] Leitor pronto{}.".format("" if versao is None else " (chip {})".format(hex(versao))))

    def disponivel(self):
        return self._leitor is not None

    def _ler_uid(self):
        leitor = self._leitor
        estado, _ = leitor.request(leitor.REQIDL)
        if estado != leitor.OK:
            return None
        estado, uid = leitor.anticoll()
        if estado != leitor.OK:
            return None
        # anticoll devolve 5 bytes: os 4 do UID e 1 de verificação
        return "".join("{:02X}".format(byte) for byte in uid[:4])

    def ler(self):
        """UID do cartão em hexadecimal (ex.: 'A1B2C3D4'), ou None."""
        if self._leitor is None:
            return None
        try:
            uid = self._ler_uid()
        except Exception as erro:
            print("[RFID] Erro de leitura: {}".format(erro))
            return None
        if uid is None:
            return None
        agora = time.ticks_ms()
        if uid == self._ultimo_uid and time.ticks_diff(agora, self._ultimo_em) < self._intervalo:
            return None
        self._ultimo_uid = uid
        self._ultimo_em = agora
        print("[RFID] Cartão lido: {}".format(uid))
        return uid


# ======================================================================
# Buzzer e LED de aprovação
# ======================================================================

class Sinalizador:
    """Bipes e LED que confirmam cada ação para o cliente.

    Os bipes NÃO travam o laço principal. Um bipe de erro dura uns 200 ms;
    se o programa ficasse parado esperando (sleep), uma tecla apertada e
    solta nesse meio-tempo seria perdida (foi o que aconteceu nos testes
    com "* * *" rápido). Por isso cada bipe vira uma lista de passos
    (liga 60 ms, desliga 80 ms, liga 60 ms...) e o main.py chama
    atualizar() a cada volta do laço para avançar de um passo ao outro.
    """

    def __init__(self, pino_buzzer=PINO_BUZZER, pino_led=PINO_LED_APROVADO):
        self._buzzer = Pin(pino_buzzer, Pin.OUT, value=0)
        self._led = Pin(pino_led, Pin.OUT, value=0)
        self._passos = []          # [(nível do buzzer, duração em ms), ...]
        self._passo_desde = 0
        self._passo_duracao = None  # None = nenhum bipe em andamento

    def _bipar(self, vezes, duracao_ms, pausa_ms=80):
        passos = []
        for vez in range(vezes):
            passos.append((1, duracao_ms))
            if vez < vezes - 1:
                passos.append((0, pausa_ms))
        self._passos = passos      # um bipe novo substitui o anterior
        self._proximo_passo()

    def _proximo_passo(self):
        if not self._passos:
            self._buzzer.off()
            self._passo_duracao = None
            return
        nivel, duracao = self._passos.pop(0)
        self._buzzer.value(nivel)
        self._passo_desde = time.ticks_ms()
        self._passo_duracao = duracao

    def atualizar(self):
        """Chamado a cada volta do laço: avança o bipe em andamento."""
        if (self._passo_duracao is not None
                and time.ticks_diff(time.ticks_ms(), self._passo_desde) >= self._passo_duracao):
            self._proximo_passo()

    def tecla(self):
        self._bipar(1, 25)

    def erro(self):
        self._bipar(2, 60)

    def aprovado(self):
        self._led.on()
        self._bipar(1, 300)

    def recusado(self):
        self._bipar(3, 120)

    def apagar(self):
        self._led.off()
        self._passos = []
        self._passo_duracao = None
        self._buzzer.off()
