# teste_teclado.py - Mostra no console cada tecla apertada no teclado 4x4 (60 s)
# Rode no Thonny (Executar), com o hardware.py na placa. Serve para conferir
# os 8 fios: se a tecla mostrada não for a apertada, há fios trocados.
import time
from hardware import TECLADO_COLUNAS, TECLADO_LINHAS, Teclado

teclado = Teclado(TECLADO_LINHAS, TECLADO_COLUNAS)
teclado.LETRAS = {}          # aqui queremos ver as letras A-D como letras
print("Aperte as teclas (60 s). Esperado: a mesma tecla que você apertou.")
inicio = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), inicio) < 60000:
    tecla = teclado.ler()
    if tecla is not None:
        print(">>> Tecla:", tecla)
    time.sleep_ms(10)
print("Fim do teste.")
