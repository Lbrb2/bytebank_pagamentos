"""
pagamentos.py - Envio das vendas e contingência offline (MicroPython)

Uma venda segue um de dois caminhos:

  ONLINE        envia ao servidor e mostra a resposta dele (aprovada ou
                recusada, com o motivo)
  CONTINGÊNCIA  sem conexão, a venda entra na fila FIFO e é enviada depois

Na contingência o terminal não consegue consultar o saldo do cliente. Por
isso só aceita vendas até LIMITE_OFFLINE_CENTAVOS: é o risco que o banco
aceita correr. As maquininhas de verdade fazem o mesmo.

Cada venda leva um id_transacao único. Se o servidor processar uma venda e
a resposta se perder no caminho, o terminal a reenvia pela fila; com o
mesmo id, o servidor reconhece a repetição e não cobra duas vezes
(idempotência).

O modo operador também usa esta classe para cancelar a última venda
(estorno), ver o resumo do caixa e esvaziar a fila offline na hora.
"""

import os
import time

LIMITE_OFFLINE_CENTAVOS = 5000   # R$ 50,00 por venda sem conexão
ANO_MINIMO_RELOGIO_OK = 2024     # antes disso, o relógio não foi sincronizado

APROVADA = "aprovada"
RECUSADA = "recusada"
OFFLINE = "offline"


def _id_aleatorio():
    return "".join("{:02x}".format(b) for b in os.urandom(6))


def _data_hora_utc():
    """(texto ISO 8601 em UTC, relógio_confiável)."""
    ano, mes, dia, hora, minuto, segundo = time.gmtime()[:6]
    texto = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(
        ano, mes, dia, hora, minuto, segundo)
    return texto, ano >= ANO_MINIMO_RELOGIO_OK


class Pagamentos:
    def __init__(self, api, fila, terminal_id, conectado):
        self._api = api
        self._fila = fila
        self._terminal_id = terminal_id
        self._conectado = conectado            # função: True se há Wi-Fi
        self.recusadas_na_sincronizacao = 0
        self.ultima_venda = None               # para o estorno do modo operador

    def montar_transacao(self, uid_cartao, itens, total_centavos):
        criada_em, relogio_ok = _data_hora_utc()
        return {
            "id_transacao": "{}-{}".format(self._terminal_id, _id_aleatorio()),
            "terminal": self._terminal_id,
            "uid_cartao": uid_cartao,
            "itens": itens,
            # Só informativo: o servidor recalcula com os preços do banco
            "total_centavos": total_centavos,
            "criada_em": criada_em,
            "relogio_sincronizado": relogio_ok,
            "origem": "online",
        }

    def pagar(self, uid_cartao, itens, total_centavos):
        """Processa uma venda. Devolve um dicionário com a 'situacao'
        (APROVADA, RECUSADA ou OFFLINE) e os dados para a tela."""
        transacao = self.montar_transacao(uid_cartao, itens, total_centavos)
        resultado = None

        if self._api is not None and self._conectado():
            codigo, corpo = self._api.enviar_transacao(transacao)
            if codigo is not None and codigo < 500:
                resultado = self._interpretar(codigo, corpo, total_centavos)
            else:
                # Sem resposta, ou erro interno do servidor (5xx): o cartão NÃO
                # foi cobrado com certeza, então a venda segue para a contingência.
                print("[PAGAMENTO] Servidor indisponível ({}). Usando contingência.".format(
                    corpo if codigo is None else "HTTP {}".format(codigo)))

        if resultado is None:
            resultado = self._contingencia(transacao)
        if resultado["situacao"] in (APROVADA, OFFLINE):
            self.ultima_venda = {"id_transacao": transacao["id_transacao"], "itens": itens,
                                 "total_centavos": resultado["total_centavos"]}
        return resultado

    def _interpretar(self, codigo, corpo, total_centavos):
        if not isinstance(corpo, dict):
            corpo = {}
        if 200 <= codigo < 300 and corpo.get("status") == APROVADA:
            return {
                "situacao": APROVADA,
                "total_centavos": corpo.get("total_centavos", total_centavos),
                "saldo_centavos": corpo.get("saldo_centavos"),
            }
        motivo = corpo.get("motivo") or "Recusada pelo servidor (HTTP {})".format(codigo)
        return {"situacao": RECUSADA, "motivo": motivo}

    def _contingencia(self, transacao):
        total = transacao["total_centavos"]
        if total > LIMITE_OFFLINE_CENTAVOS:
            return {"situacao": RECUSADA,
                    "motivo": "Sem conexão. Limite offline: R$ {},{:02d}".format(
                        LIMITE_OFFLINE_CENTAVOS // 100, LIMITE_OFFLINE_CENTAVOS % 100)}
        transacao["origem"] = "contingencia"
        if not self._fila.enfileirar(transacao):
            return {"situacao": RECUSADA,
                    "motivo": "Sem conexão e fila offline cheia"}
        print("[PAGAMENTO] Venda {} guardada na fila offline ({} pendente(s)).".format(
            transacao["id_transacao"], len(self._fila)))
        return {"situacao": OFFLINE, "total_centavos": total}

    def sincronizar(self):
        """Envia a venda mais antiga da fila. Devolve True se a fila andou.

        É chamada periodicamente pelo main.py. Envia uma venda por vez, para
        o terminal não ficar parado muito tempo se houver muitas pendentes.
        """
        if self._api is None or self._fila.vazia() or not self._conectado():
            return False
        transacao = self._fila.frente()
        codigo, corpo = self._api.enviar_transacao(transacao)
        if codigo is None or codigo >= 500:
            return False   # continua na frente da fila; tenta de novo depois

        # O servidor decidiu (aprovou, recusou ou reconheceu como repetida):
        # em todos os casos a venda sai da fila.
        self._fila.desenfileirar()
        situacao = corpo.get("status") if isinstance(corpo, dict) else None
        if situacao == APROVADA:
            print("[SYNC] Venda {} confirmada pelo servidor.".format(transacao["id_transacao"]))
        else:
            self.recusadas_na_sincronizacao += 1
            motivo = corpo.get("motivo") if isinstance(corpo, dict) else None
            print("[SYNC] Venda {} RECUSADA na sincronização: {}".format(
                transacao["id_transacao"], motivo or "HTTP {}".format(codigo)))
        return True

    # ---------------- Modo operador ----------------

    def estornar_ultima(self):
        """Cancela a última venda deste terminal. Devolve (ok, mensagem).

        Se a venda ainda está na fila offline, basta tirá-la da fila: o
        servidor nunca soube dela. Se já foi enviada, o servidor precisa
        devolver o dinheiro ao cliente (POST /estornos).
        """
        venda = self.ultima_venda
        if venda is None:
            return False, "Nenhuma venda para cancelar"
        if self._fila.remover_ultima_se(venda["id_transacao"]):
            self.ultima_venda = None
            print("[ESTORNO] Venda {} retirada da fila offline.".format(venda["id_transacao"]))
            return True, "Venda cancelada"
        if self._api is None or not self._conectado():
            return False, "Sem conexao. Tente mais tarde"

        codigo, corpo = self._api.estornar(venda["id_transacao"])
        if codigo is None or codigo >= 500:
            return False, "Servidor fora do ar. Tente mais tarde"
        if codigo == 200:
            self.ultima_venda = None
            print("[ESTORNO] Venda {} estornada pelo servidor.".format(venda["id_transacao"]))
            return True, "Venda cancelada"
        motivo = corpo.get("motivo") if isinstance(corpo, dict) else None
        return False, motivo or "Recusado (HTTP {})".format(codigo)

    def resumo_do_caixa(self):
        """(dicionário do servidor, None) ou (None, mensagem de erro)."""
        if self._api is None or not self._conectado():
            return None, "Sem conexao"
        codigo, corpo = self._api.obter_resumo()
        if codigo == 200 and isinstance(corpo, dict):
            return corpo, None
        return None, "Servidor fora do ar" if codigo is None else "Erro HTTP {}".format(codigo)

    def pendentes(self):
        return len(self._fila)

    def enviar_fila(self):
        """Envia todas as vendas pendentes agora. Devolve quantas foram."""
        enviadas = 0
        while self.sincronizar():
            enviadas += 1
        return enviadas
