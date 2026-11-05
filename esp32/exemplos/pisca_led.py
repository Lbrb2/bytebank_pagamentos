"""
pisca_led.py - Exemplo do professor: pisca o LED para testar a placa

O ESP32 executa este arquivo automaticamente logo DEPOIS do boot.py.
O boot.py já cuidou do Wi-Fi; aqui fica o código do circuito/aplicação.

Por enquanto (Entrega 1) ele só confirma que a placa está operacional
piscando o LED de status. O fluxo de vendas entra na Entrega 2.

Baseado no exemplo do professor. Mudança: o LED da nossa placa fica no
pino 16, e não no 2 (descoberto com o teste de pinos).
"""

from machine import Pin
import time

PINO_LED = 16

led = Pin(PINO_LED, Pin.OUT)

print("MicroPython operacional no ESP32!")

# Pisca o LED 5 vezes para confirmar o funcionamento
for i in range(5):
    led.value(1)  # Liga o LED
    print(f"Piscou {i + 1}/5 - LED LIGADO")
    time.sleep(0.5)
    led.value(0)  # Desliga o LED
    time.sleep(0.5)

print("Teste concluído com sucesso!")
