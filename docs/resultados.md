# Benchmark com informes reais da CVM

**Resultado:** XGBoost foi escolhido apenas pela validação e obteve AP **0,411** contra **0,303** da regra de tendência no teste. Na fila dos 10% mais bem ranqueados, a precisão média mensal foi **49,1%** contra **46,6%**: o ganho no topo foi menor do que o ganho no ranking completo. A regressão logística perdeu para o baseline em AP.

| Método | AP na validação | AP no teste | Precisão top 10% | Recall top 10% | Brier (menor é melhor) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Regra de tendência (baseline) | 0,345 | 0,303 | 46,6% | 30,5% | — |
| Regressão logística | 0,219 | 0,220 | 25,9% | 17,0% | 0,129 |
| XGBoost | 0,480 | 0,411 | 49,1% | 32,1% | 0,106 |

## Denominadores e cronologia

- **Fonte:** 21 ZIPs oficiais da CVM. Foram **118.010** observações de fundo/classe por competência (não entidades únicas), de 2022-01 a 2026-06, das quais 92.034 tinham desfecho observado. O [relatório agregado](resultados.json) inclui os hashes dos ZIPs e das saídas, as configurações e as métricas por mês.
- **Treino, validação e teste:** 2022-01 a 2024-09; 2025-01 a 2025-06; 2025-10 a 2026-03, respectivamente. Rótulos usados no treino e na seleção maturam antes da janela seguinte.
- **Cobertura:** as três abordagens foram avaliadas nas mesmas **8.578** linhas de teste com histórico e desfecho completos; **1.302** eram positivas. O painel inclui as **18.030** linhas válidas de origem no teste, mesmo quando o modelo não pontua ou o desfecho falta. A prevalência no conjunto comparável é 15,2%.
- **Última competência da base:** 2026-06, com 3.253 registros de origem, dos quais 265 ficaram sem escore. Essa competência não entra nas métricas e não tem desfecho publicado pelo projeto.

## O que não se pode concluir

Os informativos podem ter sido retificados depois da competência. O corte temporal evita rótulos futuros no treino e na seleção, mas **não reconstrói a versão disponível no dia histórico**. Além disso, métricas incluem apenas linhas com desfecho conhecido e histórico suficiente; a fila real não saberia de antemão quais desfechos vão faltar. Portanto, não interprete esses números como desempenho da fila inteira, prova de calibração prospectiva, redução de perdas ou validação para conceder crédito. A AP e o Brier são descritivos deste recorte. O indicador e o limiar de 2 pontos percentuais são experimentais.

Reprodução: `uv run --locked python -m carteira_radar build --track`. O SQLite local do MLflow registrou 22 métricas escalares; o JSON permite inspecioná-las sem subir um servidor. A fonte é o [Informe Mensal de FIDC da CVM](https://dados.cvm.gov.br/dataset/fidc-doc-inf_mensal).
