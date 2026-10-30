"""
fila_offline.py - Fila FIFO de contingência (MicroPython)

Quando o Wi-Fi ou o servidor caem, a venda não é perdida: ela entra nesta
fila e é enviada mais tarde, quando a conexão voltar.

Por que FIFO (First In, First Out)? As vendas precisam chegar ao servidor
na MESMA ORDEM em que aconteceram. Se duas vendas usarem o mesmo cartão, a
ordem decide qual delas encontra saldo suficiente.

Como funciona por dentro:

    _itens   [ v1, v2, v3, v4 ]
               ^
            _inicio            frente da fila = _itens[_inicio]

    enfileirar     -> append() no fim da lista ............ O(1)
    desenfileirar  -> avança _inicio em uma posição ....... O(1)

Fazer pop(0) também tiraria o primeiro elemento, mas custa O(n): a lista
inteira é deslocada uma posição para a esquerda a cada remoção. Com o índice
de início, remover a frente é só somar 1. Os itens já enviados são
descartados de tempos em tempos (compactação), para não ocupar memória.

A fila é gravada na flash a cada alteração: se a placa desligar com vendas
pendentes, elas continuam lá quando ligar de novo.
"""

import arquivos

ARQUIVO = "fila_offline.json"
CAPACIDADE = 50          # limite para não esgotar a memória da placa
COMPACTAR_A_CADA = 10    # descarta os itens já enviados a cada 10 remoções


class FilaContingencia:
    def __init__(self, arquivo=ARQUIVO, capacidade=CAPACIDADE):
        self._arquivo = arquivo
        self._capacidade = capacidade
        self._itens = []
        self._inicio = 0
        self._carregar()

    def __len__(self):
        return len(self._itens) - self._inicio

    def vazia(self):
        return len(self) == 0

    def cheia(self):
        return len(self) >= self._capacidade

    def enfileirar(self, transacao):
        """Coloca a transação no fim da fila. Devolve False se estiver cheia."""
        if self.cheia():
            return False
        self._itens.append(transacao)
        self._salvar()
        return True

    def frente(self):
        """A transação mais antiga, sem removê-la. None se a fila estiver vazia."""
        if self.vazia():
            return None
        return self._itens[self._inicio]

    def desenfileirar(self):
        """Remove e devolve a transação mais antiga. None se estiver vazia."""
        if self.vazia():
            return None
        transacao = self._itens[self._inicio]
        self._itens[self._inicio] = None   # libera a referência para o coletor de lixo
        self._inicio += 1
        if self.vazia() or self._inicio >= COMPACTAR_A_CADA:
            self._compactar()
        self._salvar()
        return transacao

    def remover_ultima_se(self, id_transacao):
        """Remove a venda do FIM da fila, se for a indicada. Usado pelo modo
        operador para cancelar a última venda antes de ela ser enviada.

        Remover do fim de uma lista (pop) é O(1), como remover do início com
        o índice. Uma estrutura que tira dos dois lados é chamada de deque.
        """
        if self.vazia() or self._itens[-1].get("id_transacao") != id_transacao:
            return False
        self._itens.pop()
        if self.vazia():
            self._compactar()
        self._salvar()
        return True

    def pendentes(self):
        """Cópia das transações na ordem em que serão enviadas."""
        return self._itens[self._inicio:]

    def _compactar(self):
        self._itens = self._itens[self._inicio:]
        self._inicio = 0

    def _salvar(self):
        return arquivos.gravar_json(self._arquivo, self.pendentes())

    def _carregar(self):
        dados, situacao = arquivos.ler_json(self._arquivo)
        if situacao == arquivos.OK and isinstance(dados, list):
            self._itens = [item for item in dados if isinstance(item, dict)]
            if self._itens:
                print("[FILA] {} venda(s) offline pendente(s) recuperada(s) da flash.".format(
                    len(self._itens)))
        elif situacao == arquivos.CORROMPIDO or situacao == arquivos.OK:
            # Não apaga: guarda com outro nome, para não perder vendas de vez
            destino = arquivos.guardar_corrompido(self._arquivo)
            print("[FILA] Arquivo da fila corrompido. Guardado como {} "
                  "para análise; a fila recomeça vazia.".format(destino))
