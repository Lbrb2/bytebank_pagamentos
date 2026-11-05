# ==============================================================================
# PROJETO: Controle de Periféricos & Telemetria Serial em MicroPython (ESP32)
# DISCIPLINA: Algoritmo e Estrutura de Dados (BD015) - CESAR School
# ==============================================================================
# Exemplo do professor. Mudança: LED digital no pino 16 (LED da nossa placa).
# Precisa do circuito montado: LED + resistor no GPIO 4, botão no GPIO 15
# (outro lado no GND) e potenciômetro no GPIO 34 (pontas em 3V3 e GND).
# Para rodar: abra no Thonny e clique em Executar. Para parar: STOP.

from machine import Pin, ADC, PWM
import time

# ------------------------------------------------------------------------------
# 1. Mapeamento e Configuração dos Pinos de Hardware
# ------------------------------------------------------------------------------

# LED Digital (LED da placa)
led_digital = Pin(16, Pin.OUT)

# LED Analógico (Controle por Modulação de Largura de Pulso - PWM)
# Frequência recomendada de 5000 Hz para LEDs
led_pwm = PWM(Pin(4), freq=5000)

# Botão Digital (Entrada com Resistor PULL_UP interno ativado)
# Pressionado = 0 (GND), Solto = 1 (VCC)
botao = Pin(15, Pin.IN, Pin.PULL_UP)

# Potenciômetro Analógico (Conversor Analógico-Digital - ADC)
# Atenuação ATTN_11DB permite leitura completa de 0V a 3.3V (Escala 0 a 4095)
potenciometro = ADC(Pin(34))
potenciometro.atten(ADC.ATTN_11DB)

print("------------------------------------------------------------------")
print("Sistema MicroPython Inicializado no ESP32!")
print("Sinalização: Pressione o botão para alternar o LED Digital.")
print("Ajuste o potenciômetro para variar o brilho do LED PWM.")
print("------------------------------------------------------------------")

# ------------------------------------------------------------------------------
# 2. Loop Principal de Execução e Telemetria
# ------------------------------------------------------------------------------
contador = 0

while True:
    contador += 1

    # --- Leitura Digital ---
    # Como usamos PULL_UP: estado_botao == 0 significa PRESSIONADO
    estado_botao = botao.value()

    if estado_botao == 0:
        led_digital.value(1)  # Liga o LED Digital
        status_botao_str = "PRESSIONADO [LED LIGADO]"
    else:
        led_digital.value(0)  # Desliga o LED Digital
        status_botao_str = "SOLTO [LED DESLIGADO]"

    # --- Leitura Analógica ---
    # Leitura do valor bruto do ADC (0 a 4095 para resolução de 12 bits)
    valor_adc = potenciometro.read()

    # Conversão do valor ADC para Volts (0V a 3.3V)
    tensao_volts = (valor_adc / 4095.0) * 3.3

    # --- Controle Analógico (PWM) ---
    # No ESP32 MicroPython padrão, o duty cycle do PWM varia de 0 a 1023
    # Mapeamos a leitura do ADC (0-4095) para o PWM (0-1023) dividindo por 4
    duty_pwm = valor_adc // 4
    led_pwm.duty(duty_pwm)

    # --- Transmissão de Dados pela Porta Serial (f-string) ---
    print(f"[{contador:04d}] Botão: {status_botao_str:<25} | "
          f"ADC: {valor_adc:04d} ({tensao_volts:.2f}V) | "
          f"Duty PWM: {duty_pwm:04d}/1023")

    # Aguarda 300ms antes da próxima leitura
    time.sleep(0.3)
