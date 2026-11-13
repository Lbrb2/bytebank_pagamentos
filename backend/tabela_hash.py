"""
tabela_hash.py - Tabela hash implementada do zero (AED)

O backend usa três tabelas hash para achar as coisas em O(1), sem varrer
listas nem consultar o banco a cada pacote que o terminal envia:

    cartões      UID do cartão RFID   -> cartão e conta
    produtos     código da tecla      -> produto (nome e preço)
    processadas  id_transacao         -> resposta já enviada (idempotência)

Como funciona:

    chave "532F07FE" --hash--> 2459436973 --resto da divisão por 16--> balde 13
    chave "7"        --hash-->  839689206 --resto da divisão por 16--> balde 6

    baldes:   0 []
              ...
              6 [("7", Chocolate)]
              ...
             13 [("532F07FE", ...), (outra chave)]   <- colisão: duas no mesmo balde
              ...

  * A função de hash (FNV-1a) transforma a chave num número. O resto da
    divisão pelo número de baldes diz em qual balde ela fica.
  * Duas chaves diferentes podem cair no mesmo balde (colisão). Elas ficam
    numa pequena lista dentro do balde: é o ENCADEAMENTO SEPARADO.
  * Quando a tabela passa de 75% de ocupação (fator de carga), o número de
    baldes dobra e tudo é redistribuído. Assim os baldes continuam com 0 ou
    1 elemento em média, e buscar, inserir e remover custam O(1) em média.

O dict do Python também é uma tabela hash (com outra técnica de colisão,
o endereçamento aberto). Esta classe existe para mostrar o mecanismo.
"""

FNV_OFFSET = 2166136261
FNV_PRIMO = 16777619


def fnv1a(chave):
    """Hash FNV-1a de 32 bits sobre os bytes da chave (em UTF-8).

    Para cada byte: mistura com XOR e multiplica por um número primo. Chaves
    parecidas ("POS-01", "POS-02") geram números bem diferentes, o que
    espalha as chaves pelos baldes.
    """
    valor = FNV_OFFSET
    for byte in str(chave).encode("utf-8"):
        valor ^= byte
        valor = (valor * FNV_PRIMO) & 0xFFFFFFFF
    return valor


class TabelaHash:
    CAPACIDADE_INICIAL = 16
    FATOR_DE_CARGA_MAXIMO = 0.75

    def __init__(self, capacidade=CAPACIDADE_INICIAL):
        self._baldes = [[] for _ in range(max(1, capacidade))]
        self._tamanho = 0

    # ---------------- Operações O(1) em média ----------------

    def inserir(self, chave, valor):
        """Guarda o valor da chave. Se a chave já existe, troca o valor."""
        balde = self._balde(chave)
        for posicao, (existente, _) in enumerate(balde):
            if existente == chave:
                balde[posicao] = (chave, valor)
                return
        balde.append((chave, valor))
        self._tamanho += 1
        if self.fator_de_carga() > self.FATOR_DE_CARGA_MAXIMO:
            self._redimensionar(len(self._baldes) * 2)

    def buscar(self, chave, padrao=None):
        """Valor da chave, ou `padrao` se ela não existir."""
        for existente, valor in self._balde(chave):
            if existente == chave:
                return valor
        return padrao

    def remover(self, chave):
        """Remove a chave. Devolve True se ela existia."""
        balde = self._balde(chave)
        for posicao, (existente, _) in enumerate(balde):
            if existente == chave:
                del balde[posicao]
                self._tamanho -= 1
                return True
        return False

    def __contains__(self, chave):
        sentinela = object()
        return self.buscar(chave, sentinela) is not sentinela

    def __len__(self):
        return self._tamanho

    def itens(self):
        """Todos os pares (chave, valor), sem ordem definida."""
        return [par for balde in self._baldes for par in balde]

    # ---------------- Diagnóstico (útil na apresentação) ----------------

    def fator_de_carga(self):
        return self._tamanho / len(self._baldes)

    def estatisticas(self):
        ocupados = [len(balde) for balde in self._baldes if balde]
        return {
            "elementos": self._tamanho,
            "baldes": len(self._baldes),
            "fator_de_carga": round(self.fator_de_carga(), 3),
            "baldes_ocupados": len(ocupados),
            "maior_balde": max(ocupados) if ocupados else 0,
        }

    # ---------------- Interno ----------------

    def _balde(self, chave):
        return self._baldes[fnv1a(chave) % len(self._baldes)]

    def _redimensionar(self, nova_capacidade):
        antigos = self.itens()
        self._baldes = [[] for _ in range(nova_capacidade)]
        for chave, valor in antigos:
            self._baldes[fnv1a(chave) % nova_capacidade].append((chave, valor))
