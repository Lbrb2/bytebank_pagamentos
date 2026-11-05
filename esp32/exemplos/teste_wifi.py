# teste_wifi.py - Descobre por que o ESP32 não conecta numa rede
# Rode no Thonny (Executar). Não precisa salvar na placa.
import network
import time
import config

TIPOS = {0: "aberta", 1: "WEP", 2: "WPA", 3: "WPA2", 4: "WPA/WPA2",
         5: "WPA2-Enterprise", 6: "WPA3", 7: "WPA2/WPA3"}

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
try:
    wlan.disconnect()
except OSError:
    pass

print("Nome no config.py: {!r} ({} letras)".format(config.WIFI_SSID, len(config.WIFI_SSID)))
print("Senha no config.py: {} caracteres".format(len(config.WIFI_SENHA)))
print("Senha tem espaço no começo/fim?", config.WIFI_SENHA != config.WIFI_SENHA.strip())

print("\nProcurando redes...")
achou = False
for nome, bssid, canal, sinal, tipo, oculta in wlan.scan():
    nome = nome.decode("utf-8", "ignore")
    if nome == config.WIFI_SSID:
        achou = True
        print("  ACHEI: canal {} | sinal {} dBm | segurança {}".format(
            canal, sinal, TIPOS.get(tipo, tipo)))
if not achou:
    print("  A rede do config.py não apareceu na busca.")

for tentativa in range(1, 4):
    print("\nTentativa {}...".format(tentativa))
    wlan.connect(config.WIFI_SSID, config.WIFI_SENHA)
    inicio = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), inicio) < 15000:
        if wlan.isconnected():
            break
        time.sleep_ms(500)
    print("  status final:", wlan.status(), "| conectado:", wlan.isconnected())
    if wlan.isconnected():
        print("  CONECTOU! IP:", wlan.ifconfig()[0])
        break
    try:
        wlan.disconnect()
    except OSError:
        pass
    time.sleep(2)

print("\nCódigos: 1000=parado 1001=conectando 202=senha incorreta 201=rede não encontrada")
print("         203=falha de associação 204=tempo esgotado no aperto de mão 1010=conectado")
