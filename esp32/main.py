"""
main.py - Aplicação do terminal ByteBank Pay (ESP32 / MicroPython)

Roda automaticamente depois do boot.py. Monta as peças e executa o laço
principal, que a cada volta (~20 ms):

  1. lê o botão e repassa o evento (toque, segurar...) ao terminal;
  2. se o terminal espera um cartão, consulta o leitor RFID;
  3. trata os tempos limite das telas;
  4. de tempos em tempos: envia vendas da fila offline, busca comandos do
     gerente, recarrega o catálogo (preços e estoque), manda telemetria e
     tenta reconectar o Wi-Fi.

O laço nunca fica parado esperando algo. As tarefas mais demoradas (como
reconectar o Wi-Fi) só rodam quando ninguém está usando o terminal.

Para interromper pelo Thonny: clique no Shell e pressione Ctrl+C.
"""

import gc
import sys
import time

import wifi
from api import ClienteAPI
from carrinho import Carrinho
from catalogo import Catalogo
from comandos import Comandos
from fila_offline import FilaContingencia
from hardware import PINO_BOTAO, Botao, Display, LeitorRFID, Sinalizador
from pagamentos import Pagamentos
from terminal import Terminal

VERSAO = "0.4 (Entrega 2)"

PAUSA_DO_LACO_MS = 20
INTERVALO_SINCRONIZACAO_MS = 5000      # envia uma venda pendente a cada 5 s
INTERVALO_TELEMETRIA_MS = 60000
INTERVALO_COMANDOS_MS = 10000          # pergunta ao servidor se há comando novo
INTERVALO_COMANDOS_SEM_RESPOSTA_MS = 60000   # servidor fora do ar: pergunta menos
INTERVALO_CATALOGO_MS = 300000         # recarrega preços e estoque a cada 5 min
INTERVALO_RECONEXAO_MS = 30000
OCIOSO_PARA_RECONECTAR_MS = 10000      # só reconecta sem ninguém usando há 10 s
OCIOSO_PARA_COMANDOS_MS = 3000         # só busca comandos sem usar o botão há 3 s
REINICIAR_APOS_ERRO = True             # em produção, um erro inesperado reinicia a placa
ESPERA_ANTES_DE_REINICIAR_S = 10       # tempo para apertar Ctrl+C antes do reinício


def carregar_configuracao():
    try:
        import config
        return config
    except ImportError:
        print("[MAIN] config.py não encontrado: o terminal funcionará só offline.")
        return None


def passou(agora, desde, intervalo_ms):
    return time.ticks_diff(agora, desde) >= intervalo_ms


def montar_telemetria(fila, pagamentos, catalogo, display, rfid, terminal):
    info = wifi.informacoes() or {}
    gc.collect()
    return {
        "versao": VERSAO,
        "online": wifi.conectado(),
        "rssi": info.get("rssi"),
        "memoria_livre": gc.mem_free(),
        "fila_pendente": len(fila),
        "recusadas_na_sincronizacao": pagamentos.recusadas_na_sincronizacao,
        "catalogo_origem": catalogo.origem,
        "display_ok": display.disponivel(),
        "rfid_ok": rfid.disponivel(),
        "bloqueado": terminal.bloqueado(),
    }


def principal():
    print("[MAIN] ByteBank Pay {}".format(VERSAO))
    config = carregar_configuracao()

    display = Display()
    display.mostrar(["", "  BYTEBANK PAY", "", "  Iniciando..."])
    sinal = Sinalizador()
    pino_botao = getattr(config, "PINO_BOTAO", PINO_BOTAO) if config is not None else PINO_BOTAO
    botao = Botao(pino_botao, ao_segurar=sinal.tecla)   # bipe ao completar 0,8 s: já pode soltar
    rfid = LeitorRFID()

    api = None
    terminal_id = "POS-??"
    if config is not None:
        terminal_id = getattr(config, "TERMINAL_ID", terminal_id)
        if getattr(config, "API_URL", None):
            api = ClienteAPI(config.API_URL, terminal_id)

    catalogo = Catalogo()
    catalogo.iniciar(api if wifi.conectado() else None)
    fila = FilaContingencia()
    pagamentos = Pagamentos(api, fila, terminal_id, wifi.conectado)

    pin = getattr(config, "PIN_OPERADOR", None) if config is not None else None
    terminal = Terminal(display, sinal, catalogo, Carrinho(), pagamentos,
                        conectado=wifi.conectado,
                        relogio_ms=time.ticks_ms, diferenca_ms=time.ticks_diff,
                        pin_operador=pin)
    comandos = Comandos(api, terminal, catalogo)
    comandos.restaurar_bloqueio()

    ultima_sincronizacao = ultima_telemetria = ultima_reconexao = time.ticks_ms()
    ultimo_comando = ultimo_catalogo = time.ticks_ms()
    intervalo_comandos = INTERVALO_COMANDOS_MS
    print("[MAIN] Terminal pronto. Fila offline: {} pendente(s).".format(len(fila)))

    while True:
        evento = botao.ler()
        if evento is not None:
            terminal.tecla(evento)

        if terminal.aguardando_cartao():
            uid = rfid.ler()
            if uid is not None:
                terminal.cartao(uid)

        terminal.tick()
        sinal.atualizar()
        agora = time.ticks_ms()

        # Fila offline: uma venda por vez, e nunca no meio de um pagamento
        if passou(agora, ultima_sincronizacao, INTERVALO_SINCRONIZACAO_MS):
            ultima_sincronizacao = agora
            if not terminal.aguardando_cartao():
                pagamentos.sincronizar()

        # Comandos do gerente: só com ninguém comprando nem digitando, porque
        # a consulta ao servidor segura o laço por alguns instantes
        if (api is not None and wifi.conectado() and terminal.ocioso(OCIOSO_PARA_COMANDOS_MS)
                and passou(agora, ultimo_comando, intervalo_comandos)):
            respondeu = comandos.verificar()
            ultimo_comando = time.ticks_ms()
            intervalo_comandos = (INTERVALO_COMANDOS_MS if respondeu
                                  else INTERVALO_COMANDOS_SEM_RESPOSTA_MS)

        # Preços e estoque em dia, mesmo sem o gerente mandar atualizar
        if (api is not None and wifi.conectado() and terminal.ocioso(OCIOSO_PARA_RECONECTAR_MS)
                and passou(agora, ultimo_catalogo, INTERVALO_CATALOGO_MS)):
            ultimo_catalogo = time.ticks_ms()
            if catalogo.atualizar(api):
                terminal.catalogo_atualizado()

        if api is not None and wifi.conectado() and passou(agora, ultima_telemetria, INTERVALO_TELEMETRIA_MS):
            ultima_telemetria = agora
            api.enviar_telemetria(montar_telemetria(fila, pagamentos, catalogo, display, rfid, terminal))

        # Reconexão: só com o terminal parado, porque leva alguns segundos
        if (config is not None and not wifi.conectado()
                and passou(agora, ultima_reconexao, INTERVALO_RECONEXAO_MS)
                and terminal.ocioso(OCIOSO_PARA_RECONECTAR_MS)):
            ultima_reconexao = time.ticks_ms()
            print("[MAIN] Sem Wi-Fi. Tentando reconectar...")
            if wifi.conectar(config.WIFI_SSID, config.WIFI_SENHA, tentativas=1, tempo_limite_ms=5000):
                if catalogo.origem != "servidor" and api is not None:
                    catalogo.iniciar(api)
            terminal.redesenhar()

        time.sleep_ms(PAUSA_DO_LACO_MS)


def mostrar_erro(erro):
    if hasattr(sys, "print_exception"):
        sys.print_exception(erro)       # MicroPython: mostra a linha do erro
    else:
        print(repr(erro))


try:
    principal()
except KeyboardInterrupt:
    print("[MAIN] Interrompido pelo usuário (Ctrl+C).")
except Exception as erro:
    print("[MAIN] ERRO INESPERADO:")
    mostrar_erro(erro)
    if REINICIAR_APOS_ERRO:
        print("[MAIN] Reiniciando em {} s (Ctrl+C para cancelar)...".format(
            ESPERA_ANTES_DE_REINICIAR_S))
        time.sleep(ESPERA_ANTES_DE_REINICIAR_S)
        import machine
        machine.reset()
