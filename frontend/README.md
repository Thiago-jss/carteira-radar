# Carteira Radar — painel de risco

Aplicação Angular 22 em português, somente leitura. Requer Node compatível com `engines` em `package.json`.

```sh
npm ci
npm test
npm run build
```

Sirva `dist/carteira-radar/browser` na mesma origem do FastAPI, direcionando `/api/v1/*` à API, não ao `index.html`. A compilação não inclui fontes externas nem CSS crítico embutido. `npm start` inicia o servidor de desenvolvimento do Angular; para testar a integração use a mesma origem ou configure um proxy local para `/api/v1`.

- A primeira consulta usa a competência padrão da API (`GET /api/v1/predictions?limit=50`). Selecionar uma competência acrescenta `month=YYYY-MM`.
- A lista é limitada a 50. A cobertura usa a contagem integral de escores quando a API a fornece. Se a resposta for parcial e não trouxer essa contagem, a cobertura integral fica indisponível.
- Probabilidade e razões são frações formatadas em percentuais; variação é em pontos percentuais. `—` indica ausência, não zero. Rótulo e razão posterior são dados retrospectivos, não sinais do modelo.
- O histórico consulta `GET /api/v1/entities/{entity_id}` e a seção de avaliação consulta `GET /api/v1/benchmark`. Campos de avaliação não padronizados são exibidos como retornados, sem inferência de unidades.

Ferramenta de apoio à operação de risco; não é recomendação de investimento.
