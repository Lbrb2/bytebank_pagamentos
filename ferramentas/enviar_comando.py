"""
enviar_comando.py - Faz o papel do gerente: manda comandos para um terminal

Até o dashboard da Entrega 4 existir, é por aqui que se testa o caminho
gerente -> servidor -> terminal. Usa só a biblioteca padrão do Python.

Exemplos (com o servidor de teste rodando):

    python ferramentas/enviar_comando.py catalogo
    python ferramentas/enviar_comando.py mensagem "Promocao: cafe a R$ 4,00" --segundos 15
    python ferramentas/enviar_comando.py bloquear "Manutencao. Use o caixa 2"
    python ferramentas/enviar_comando.py desbloquear
    python ferramentas/enviar_comando.py preco 1 400        (muda o preço e atualiza o terminal)
    python ferramentas/enviar_comando.py estoque 7 0        (zera o estoque: o terminal mostra "esgot")

Opções: --terminal POS-01 (padrão) e --url http://127.0.0.1:8000 (padrão).
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def requisitar(metodo, url, dados):
    pedido = urllib.request.Request(url, data=json.dumps(dados).encode("utf-8"), method=metodo,
                                    headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(pedido, timeout=5) as resposta:
            return resposta.status, json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        return erro.code, json.loads(erro.read().decode("utf-8") or "{}")
    except urllib.error.URLError as erro:
        sys.exit("Servidor não respondeu em {} ({}). Ele está rodando?".format(url, erro.reason))


def main():
    parser = argparse.ArgumentParser(description="Envia comandos do gerente para um terminal.")
    parser.add_argument("--terminal", default="POS-01")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="acao", required=True)
    sub.add_parser("catalogo", help="terminal busca preços e estoque de novo")
    p = sub.add_parser("mensagem", help="mostra um aviso na tela")
    p.add_argument("texto")
    p.add_argument("--segundos", type=int, default=10)
    p = sub.add_parser("bloquear", help="trava o terminal")
    p.add_argument("motivo", nargs="?", default="Fale com o gerente")
    sub.add_parser("desbloquear", help="destrava o terminal")
    p = sub.add_parser("preco", help="muda o preço de um produto (em centavos)")
    p.add_argument("codigo")
    p.add_argument("centavos", type=int)
    p = sub.add_parser("estoque", help="muda o estoque de um produto")
    p.add_argument("codigo")
    p.add_argument("quantidade", type=int)
    args = parser.parse_args()

    base = args.url.rstrip("/")
    url_comandos = "{}/terminais/{}/comandos".format(base, args.terminal)

    if args.acao in ("preco", "estoque"):
        campo = "preco_centavos" if args.acao == "preco" else "estoque"
        valor = args.centavos if args.acao == "preco" else args.quantidade
        codigo, resposta = requisitar("PATCH", "{}/produtos/{}".format(base, args.codigo), {campo: valor})
        print("Produto: HTTP {} {}".format(codigo, resposta))
        if codigo != 200:
            sys.exit(1)
        comando = {"tipo": "atualizar_catalogo"}
    elif args.acao == "catalogo":
        comando = {"tipo": "atualizar_catalogo"}
    elif args.acao == "mensagem":
        comando = {"tipo": "mensagem", "texto": args.texto, "segundos": args.segundos}
    elif args.acao == "bloquear":
        comando = {"tipo": "bloquear", "motivo": args.motivo}
    else:
        comando = {"tipo": "desbloquear"}

    codigo, resposta = requisitar("POST", url_comandos, comando)
    print("Comando: HTTP {} {}".format(codigo, resposta))
    if codigo == 201:
        print("O terminal {} executa na próxima consulta (até 10 s na placa).".format(args.terminal))


if __name__ == "__main__":
    main()
