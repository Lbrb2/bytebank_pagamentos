"""
senhas.py - Senha do cartão (PIN de 4 dígitos), guardada só como hash

Regras do ByteBank Pay:

  * Compras ONLINE a partir de LIMITE_SEM_SENHA_CENTAVOS (R$ 20,00) pedem a
    senha. Abaixo disso a compra vale como "aproximação", sem senha, como
    nas maquininhas de verdade.
  * O banco NUNCA guarda a senha, só um hash PBKDF2-SHA256 com "salt"
    aleatório e 100 mil iterações. Quem copiar o banco não descobre as
    senhas: teria de testar as 10 mil combinações, cada uma 100 mil vezes
    mais cara que um SHA-256 comum, e de novo para cada cartão (o salt
    muda de um para outro).
  * 3 senhas erradas SEGUIDAS bloqueiam o cartão; uma certa zera a conta.
    O gerente desbloqueia pelo painel, como no bloqueio da antifraude.
  * Vendas da FILA OFFLINE não pedem senha: sem rede, a placa não tem como
    conferir. Elas já são limitadas a R$ 50,00 pelo terminal.
  * Cartão sem senha cadastrada (pin_hash vazio) não pede senha.

Formato guardado na coluna cartoes.pin_hash:
    pbkdf2_sha256$<iterações>$<salt em hex>$<hash em hex>
"""

import hashlib
import hmac
import os

LIMITE_SEM_SENHA_CENTAVOS = 2000
TENTATIVAS = 3
TAMANHO = 4
ITERACOES = 100_000
ALGORITMO = "pbkdf2_sha256"


def gerar_hash(senha, salt=None, iteracoes=ITERACOES):
    """Hash para guardar no banco. Cada chamada sorteia um salt novo."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, iteracoes)
    return "{}${}${}${}".format(ALGORITMO, iteracoes, salt.hex(), digest.hex())


def conferir(senha, guardado):
    """True se `senha` gera o mesmo hash que está guardado.

    A comparação usa hmac.compare_digest, que leva o mesmo tempo acerte ou
    erre: medir o tempo da resposta não revela quantos bytes bateram.
    """
    try:
        algoritmo, iteracoes, salt, digest = guardado.split("$")
        if algoritmo != ALGORITMO:
            return False
        calculado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), int(iteracoes))
        return hmac.compare_digest(calculado.hex(), digest)
    except (AttributeError, ValueError):
        return False


def precisa_de_senha(total_centavos, origem, pin_hash):
    return origem == "online" and bool(pin_hash) and total_centavos >= LIMITE_SEM_SENHA_CENTAVOS


def reais(centavos):
    return "R$ {},{:02d}".format(centavos // 100, centavos % 100)


if __name__ == "__main__":
    # Gera o hash de uma senha para colar no seed.sql:  python -m backend.senhas 1234
    import sys
    print(gerar_hash(sys.argv[1]))
