"""
diagnostico.py - Testa as peças da bancada, uma de cada vez

Use ANTES do main.py, sempre que montar ou mexer no circuito.

No Thonny:
  1. envie para a placa o rc522.py (Enviar para /);
  2. abra ESTE arquivo no computador e clique em Executar (▶).
     Não precisa salvar o diagnostico.py na placa.

O teste tem 3 partes: display (sozinho), botão (10 s apertando) e leitor
RFID (15 s aproximando o cartão e o chaveiro). No fim, mostra um resumo
com os pinos que funcionaram. Se algum pino da ligação for diferente,
mude as constantes abaixo e rode de novo.
"""

import time
from machine import Pin, SoftI2C

# ---------- Ligação usada na bancada (mude aqui se ligar diferente) ----------
PINO_BOTAO = 32                  # botão: um terminal no GPIO 32, o outro no GND
PINO_BOOT = 0                    # botão BOOT da própria placa (não precisa de fio)
RFID_SCK, RFID_MOSI, RFID_MISO = 18, 23, 19
RFID_SDA, RFID_RST = 27, 26

# O display já vem soldado na placa. Cada fabricante usa pinos diferentes,
# então o teste procura nos pares mais comuns: (SDA, SCL)
CANDIDATOS_OLED = [(5, 4), (4, 5), (21, 22), (4, 15)]

resumo = {}


def testar_display():
    print("\n=== 1. DISPLAY OLED ===")
    for sda, scl in CANDIDATOS_OLED:
        if (sda, scl) == (4, 15):
            Pin(16, Pin.OUT, value=1)     # placas Heltec: tira o display do reset
        try:
            i2c = SoftI2C(sda=Pin(sda), scl=Pin(scl), freq=400000)
            enderecos = i2c.scan()
        except Exception as erro:
            print("  SDA={} SCL={}: erro {}".format(sda, scl, erro))
            continue
        print("  SDA={:2d} SCL={:2d}: {}".format(sda, scl, [hex(e) for e in enderecos] or "nada"))
        endereco = 0x3C if 0x3C in enderecos else (0x3D if 0x3D in enderecos else None)
        if endereco is None:
            continue
        resumo["display"] = "SDA={} SCL={} endereco={}".format(sda, scl, hex(endereco))
        try:
            import ssd1306
        except ImportError:
            print("  >>> Display encontrado, mas falta o driver. Com Wi-Fi, no Shell:")
            print("      import mip; mip.install('ssd1306')")
            return
        oled = ssd1306.SSD1306_I2C(128, 64, i2c, addr=endereco)
        oled.fill(0)
        oled.text("ByteBank Pay", 0, 0)
        oled.text("Display OK", 0, 16)
        oled.text("SDA={} SCL={}".format(sda, scl), 0, 32)
        oled.text("12345678901234567", 0, 56)
        oled.show()
        print("  >>> Mensagem enviada ao display. Confira se apareceu.")
        return
    print("  >>> Nenhum display encontrado nesses pinos.")
    resumo["display"] = "NAO ENCONTRADO"


def testar_botao(segundos=10):
    print("\n=== 2. BOTOES (GPIO {} e botao BOOT, GPIO {}) ===".format(PINO_BOTAO, PINO_BOOT))
    print("  Durante {} s, aperte o botão azul E os botões pequenos da placa.".format(segundos))
    print("  (Um dos pequenos é o RESET: ele reinicia a placa. Se isso acontecer,")
    print("   rode de novo e aperte só o outro.)")
    botoes = {}
    for numero in (PINO_BOTAO, PINO_BOOT):
        pino = Pin(numero, Pin.IN, Pin.PULL_UP)
        if pino.value() == 0:
            print("  >>> GPIO {} já está em 0 sem apertar: terminais errados (use DIAGONAL)".format(numero))
            print("      ou o fio está encostando em outro.")
        botoes[numero] = {"pino": pino, "desde": None, "toques": 0}
    inicio = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), inicio) < segundos * 1000:
        for numero, b in botoes.items():
            apertado = b["pino"].value() == 0
            if apertado and b["desde"] is None:
                b["desde"] = time.ticks_ms()
            elif not apertado and b["desde"] is not None:
                duracao = time.ticks_diff(time.ticks_ms(), b["desde"])
                if duracao >= 30:                   # ignora a trepidação do contato
                    b["toques"] += 1
                    print("  GPIO {}: toque de {} ms ({})".format(
                        numero, duracao, "SEGURADO" if duracao >= 800 else "curto"))
                b["desde"] = None
        time.sleep_ms(10)
    partes = ["GPIO {}: {} toque(s)".format(n, b["toques"]) for n, b in botoes.items()]
    resumo["botao"] = " | ".join(partes)
    if botoes[PINO_BOOT]["toques"] and not botoes[PINO_BOTAO]["toques"]:
        print("  >>> Só o botão BOOT funcionou. Para usar ele no terminal, coloque")
        print("      PINO_BOTAO = 0 no config.py.")


def testar_rfid(segundos=15):
    print("\n=== 3. LEITOR RFID RC522 ===")
    try:
        from rc522 import RC522
    except ImportError:
        print("  >>> Envie o rc522.py para a placa e rode de novo.")
        resumo["rfid"] = "SEM rc522.py"
        return
    try:
        leitor = RC522(sck=RFID_SCK, mosi=RFID_MOSI, miso=RFID_MISO, rst=RFID_RST, cs=RFID_SDA)
        versao = leitor.versao()
    except Exception as erro:
        print("  >>> Erro ao iniciar o leitor: {}".format(erro))
        resumo["rfid"] = "ERRO {}".format(erro)
        return
    print("  Versão do chip: {}".format(hex(versao)))
    if versao in (0x00, 0xFF):
        print("  >>> O leitor não respondeu. Confira os fios SDA, SCK, MOSI, MISO, RST,")
        print("      e se o 3.3V e o GND estão ligados (NÃO use 5 V).")
        resumo["rfid"] = "NAO RESPONDEU ({})".format(hex(versao))
        return
    print("  Leitor OK. Aproxime o cartão e o chaveiro durante {} s...".format(segundos))
    inicio = time.ticks_ms()
    vistos = []
    while time.ticks_diff(time.ticks_ms(), inicio) < segundos * 1000:
        estado, _ = leitor.request(leitor.REQIDL)
        if estado == leitor.OK:
            estado, dados = leitor.anticoll()
            if estado == leitor.OK:
                uid = "".join("{:02X}".format(b) for b in dados[:4])
                if uid not in vistos:
                    vistos.append(uid)
                    print("  >>> Cartão lido: UID {}".format(uid))
        time.sleep_ms(100)
    resumo["rfid"] = ", ".join(vistos) if vistos else "leitor OK, nenhum cartão lido"
    if vistos:
        print("  Copie esses UIDs para a tabela CARTOES do servidor_teste/servidor.py.")


testar_display()
testar_botao()
testar_rfid()
print("\n=== RESUMO ===")
for peca in ("display", "botao", "rfid"):
    print("  {:8s} {}".format(peca, resumo.get(peca, "?")))
print("Mande este resumo para a equipe / para o README.")
