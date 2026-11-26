# Dados e variável-alvo

`carteira_radar.data.build_dataset(raw: pathlib.Path, start: str, end: str)` retorna `(DataFrame, dict)`. `start` e `end` são competências `YYYY-MM`, inclusive. Use um diretório de cache gravável para `raw`.

## Fonte, cache e cobertura

A fonte é o [Informe Mensal de FIDC da CVM](https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/). Para 2022–2024, procura-se primeiro o ZIP anual `HIST/inf_mensal_fidc_AAAA.zip`. Quando necessário, incluindo nos demais anos, procura-se o ZIP mensal `inf_mensal_fidc_AAAAMM.zip` diretamente em `DADOS/`. A ingestão usa somente as tabelas V e VI da mesma competência e do mesmo ZIP. Os arquivos locais são reutilizados sem atualização automática; downloads são limitados, não seguem redirecionamentos e só são publicados no cache por substituição atômica. Limites: 256 MiB por ZIP, 32 MiB e 100 mil registros por CSV.

Os metadados incluem `archives` (URL oficial esperada, SHA-256 e tamanho dos ZIPs lidos), `coverage` (estado, fonte, número de linhas aceitas e rejeições por motivo em cada mês) e `missing_months`. Um mês sem o arquivo mensal (HTTP 404) e sem as tabelas anuais é declarado ausente. Falha de transporte, arquivo inválido, par incompleto sem substituto ou duplicata com valores conflitantes interrompe a coleta. Cópias idênticas da mesma linha contam uma vez, com ocorrência registrada na cobertura. Um mês carregado pode não ter observações elegíveis. Para reproduzir uma execução, preserve os ZIPs e compare os hashes.

**Não é uma base point-in-time:** os arquivos disponíveis podem incorporar retificações retroativas. A competência não é a data em que o informe se tornou público. O alinhamento por competência impede usar meses futuros como features, mas não corrige retrospectivamente atrasos de publicação e revisões.

## Unidade e cálculo

- `entity_id`: `Fundo:` ou `Classe:` seguido do CNPJ validado, com 14 dígitos; não se tenta vincular um fundo a uma classe através de mudança cadastral. `name` é a denominação pública do mesmo mês. Não são usadas outras tabelas ou identificadores pessoais.
- `month`: primeiro dia da competência, sem fuso. Há no máximo uma observação por entidade e mês.
- `ratio`: soma das faixas disjuntas de 91 dias ou mais (B4–B10) das tabelas V e VI, dividida pela soma de A (a vencer) e B (vencido) das mesmas tabelas. O denominador não é patrimônio líquido. Exigem-se ambas as tabelas para a entidade, denominador positivo, valores não negativos e subtotal B exatamente igual à soma de B1–B10 em cada tabela. Ausência, valores inválidos ou incoerência não são interpretados como zero; essas observações são excluídas. Isso pode introduzir viés de seleção.
- `log_balance`: logaritmo natural do denominador. `lag_1` e `lag_3`: razões da mesma entidade exatamente 1 e 3 meses antes; `delta_3`: `ratio(t) - ratio(t-3)`. Meses ausentes não são preenchidos e não se usa histórico anterior a `start`.
- `label`: inteiro anulável. Vale 1 se `ratio(t+3) - ratio(t) >= 0,02` (2 pontos percentuais), ou 0 se a diferença for menor. A acumulação usa `Decimal` e a comparação usa frações exatas antes de converter as razões para `float`. `outcome_ratio` é a razão em t+3, quando disponível. `label_month` sempre indica o primeiro dia de t+3. Se o resultado estiver além de `end` (`beyond_end`) ou indisponível dentro da janela (`unavailable`), o rótulo fica nulo e a linha permanece disponível para pontuação.

Para treinar com corte temporal, use somente rótulos cujo `label_month` já passou no corte definido para treino. Não use `outcome_ratio`, `label`, `label_month` ou `outcome_reason` como features. Trata-se de pesquisa experimental, não de recomendação financeira.
