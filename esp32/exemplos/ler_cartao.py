# ler_cartao.py - Lê os cartões RFID por 60 segundos e mostra o UID de cada um
# Rode no Thonny (Executar). Precisa do rc522.py na placa.
import time
from rc522 import RC522

# Velocidade baixa (100 kHz): mais tolerante a fios longos e mau contato
leitor = RC522(sck=18, mosi=23, miso=19, rst=26, cs=27, baudrate=100000)

# Lê a versão 8 vezes. Se os valores mudarem, é mau contato num fio ou na solda.
versoes = [hex(leitor.versao()) for _ in range(8)]
print("Versão do chip (8 leituras):", versoes)
if len(set(versoes)) > 1:
    print(">>> As leituras mudaram: confira a solda e os fios SCK, MOSI, MISO e SDA.")
print("Encoste o cartão e o chaveiro no leitor (60 s)...")

vistos = []
inicio = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), inicio) < 60000:
    estado, _ = leitor.request(leitor.REQALL)
    if estado == leitor.OK:
        estado, dados = leitor.anticoll()
        if estado == leitor.OK:
            uid = "".join("{:02X}".format(b) for b in dados[:4])
            if uid not in vistos:
                vistos.append(uid)
                print(">>> Cartão lido: UID", uid)
    time.sleep_ms(100)

print("Fim. UIDs lidos:", vistos)
