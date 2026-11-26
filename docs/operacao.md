# Operação e limites

O treinamento em lote lê arquivos públicos de uma origem fixa, grava em diretórios novos e troca `CURRENT` só depois de validar conteúdo e checksums. O processo HTTP abre e fixa o snapshot ao primeiro acesso; depois de gerar outra versão, reinicie o serviço. Se não houver snapshot íntegro, a API responde `503`. Não há treinamento acionado por HTTP, upload de arquivos, chaves para baixar os dados ou integração com corretora.

A API e o Angular são servidos na mesma origem, em `127.0.0.1:8084`. O contêiner usa usuário não privilegiado e monta os resultados somente para leitura. O serviço confia no diretório local de resultados; checksums detectam corrupção acidental, não adulteração por alguém com acesso de escrita ao manifesto. Não aponte para artefatos de terceiros. O runtime lê JSON validado e não desserializa pickle. A configuração opcional usa `RADAR_ARTIFACTS` e `RADAR_FRONTEND`: ambos caminhos locais, nenhum segredo.

## Diagnóstico

| Sintoma | Ação |
| --- | --- |
| CVM indisponível | Consulte o erro do comando batch e preserve o `CURRENT` anterior. Não publique uma coleta parcial. |
| `503` ao abrir o painel | Confira permissões de leitura, `artifacts/CURRENT`, arquivos do run e checksums. No Compose: `docker compose logs dashboard`. |
| Dado retrospectivo diferente após novo download | Compare hashes das fontes. Uma reapresentação pode mudar a base histórica. Não sobrescreva um experimento antigo para fingir equivalência. |
| Carteira sem probabilidade | Consulte o motivo de cobertura. Ausência de sinal não equivale a risco baixo. |

`data/raw/`, `artifacts/`, bancos SQLite, MLflow local e qualquer `.env` entram no ignore do Git e não são copiados para a imagem. Apenas código, testes, documentação, um resumo agregado do benchmark e capturas aprovadas são publicados. A CI usa uma fixture sintética, não afirma qualidade da previsão.
