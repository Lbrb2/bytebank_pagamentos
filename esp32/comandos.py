"""
comandos.py - Comandos remotos enviados pelo gerente (MicroPython)

O gerente (hoje pela ferramenta enviar_comando.py; na Entrega 4, pelo
dashboard Streamlit) coloca comandos numa FILA no servidor, uma por
terminal. O terminal pergunta de tempos em tempos se há algo para ele:

    GET  /terminais/POS-01/comandos               -> {"comando": {...}} ou {"comando": null}
    (executa)
    POST /terminais/POS-01/comandos/7/confirmacao -> o servidor tira o 7 da fila

Por que o terminal pergunta, em vez de o servidor mandar? O ESP32 está
atrás do roteador e não tem um endereço fixo que o servidor consiga
chamar. Perguntar periodicamente (polling) funciona em qualquer rede.

Comandos aceitos:
    atualizar_catalogo   busca preços e estoque de novo
    bloquear             trava o terminal (motivo aparece na tela)
    desbloquear          volta a vender
    mensagem             mostra um aviso por alguns segundos

O comando só é tirado da fila DEPOIS de executado. Se a confirmação se
perder, o terminal recebe o mesmo comando de novo; por isso todos podem
ser repetidos sem estrago (uma mensagem, no máximo, aparece duas vezes).

O bloqueio é gravado na flash: desligar e ligar a placa não destrava o
terminal.
"""

import arquivos

ARQUIVO_BLOQUEIO = "bloqueio.json"
DURACAO_PADRAO_MENSAGEM_S = 10
DURACAO_MAXIMA_MENSAGEM_S = 60
TAMANHO_MAXIMO_TEXTO = 96      # 6 linhas de 16 caracteres


class Comandos:
    def __init__(self, api, terminal, catalogo, arquivo=ARQUIVO_BLOQUEIO):
        self._api = api
        self._terminal = terminal
        self._catalogo = catalogo
        self._arquivo = arquivo
        self.executados = 0

    def restaurar_bloqueio(self):
        """Chamado na inicialização: se o terminal estava bloqueado antes de
        desligar, continua bloqueado."""
        dados, _ = arquivos.ler_json(self._arquivo)
        if isinstance(dados, dict) and dados.get("bloqueado"):
            print("[COMANDOS] Terminal continua BLOQUEADO (gravado na flash).")
            self._terminal.bloquear(str(dados.get("motivo") or ""))

    def verificar(self):
        """Busca e executa UM comando pendente.

        Devolve True se o servidor respondeu (havendo comando ou não) e
        False se não respondeu; o main.py usa isso para perguntar menos
        vezes quando o servidor está fora do ar.
        """
        if self._api is None:
            return False
        respondeu, comando = self._api.obter_comando()
        if not respondeu:
            return False
        if comando is None:
            return True
        numero = comando.get("id")
        resultado = self._executar(comando)
        print("[COMANDOS] {} -> {}".format(comando.get("tipo"), resultado))
        if numero is not None:
            self._api.confirmar_comando(numero, resultado)
        self.executados += 1
        return True

    def _executar(self, comando):
        tipo = comando.get("tipo")
        if tipo == "atualizar_catalogo":
            if self._catalogo.atualizar(self._api):
                self._terminal.catalogo_atualizado()
                return "ok: {} produtos".format(len(self._catalogo))
            return "erro: catalogo nao recebido"

        if tipo == "bloquear":
            motivo = str(comando.get("motivo") or "Fale com o gerente")[:TAMANHO_MAXIMO_TEXTO]
            arquivos.gravar_json(self._arquivo, {"bloqueado": True, "motivo": motivo})
            self._terminal.bloquear(motivo)
            return "ok: bloqueado"

        if tipo == "desbloquear":
            arquivos.gravar_json(self._arquivo, {"bloqueado": False})
            self._terminal.desbloquear()
            return "ok: desbloqueado"

        if tipo == "mensagem":
            texto = str(comando.get("texto") or "")[:TAMANHO_MAXIMO_TEXTO]
            segundos = comando.get("segundos", DURACAO_PADRAO_MENSAGEM_S)
            if not isinstance(segundos, int) or isinstance(segundos, bool):
                segundos = DURACAO_PADRAO_MENSAGEM_S
            segundos = max(3, min(segundos, DURACAO_MAXIMA_MENSAGEM_S))
            if not texto:
                return "erro: mensagem vazia"
            if self._terminal.mostrar_mensagem(texto, segundos * 1000):
                return "ok: mostrada por {} s".format(segundos)
            return "ignorada: terminal bloqueado"

        return "ignorado: tipo desconhecido ({})".format(tipo)
