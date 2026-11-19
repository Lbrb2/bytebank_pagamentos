# Dashboard — Streamlit (Entrega 4)

Painel do gerente: vendas e terminais em tempo real e comandos para o ESP32.
Conversa só com a API do backend (nunca direto com o banco).

```bash
python -m backend                          # num terminal
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py             # em outro, da pasta RAIZ do repositório
```

| Arquivo | O que faz |
|---|---|
| `app.py` | telas: filtros, indicadores, terminais, gráficos, controle remoto, auditoria |
| `graficos.py` | gráficos em Altair e as regras de visualização usadas |
| `dados.py` | cliente da API e cálculos (indicadores, faturamento por hora, situação do terminal) |
| `placa_usb.py` | aba Placa (USB): encontra a placa, grava o `config.py`, envia o código e lê o console (usa o `mpremote`) |

O botão **Exportar relatório** baixa o período escolhido em Excel ou CSV (gerados
pelo backend em `GET /relatorios/exportar`). Precisa do Streamlit 1.52 ou mais novo.

Decisões de visualização e roteiro do teste ponta a ponta: seção
**Dashboard (Entrega 4)** do README principal.
