# Produtos (modalidades)

A organização pode vender diferentes modalidades de consórcio:
- consórcio **imobiliário**;
- consórcio de **veículos**;
- consórcio de **pesados**;
- consórcio de **serviços**;
- consórcio para **equipamentos ou finalidades específicas**, quando disponível.

## Consórcio imobiliário — usos do crédito
Conforme as regras da administradora e do contrato, o crédito pode ser usado para:
- compra de imóvel residencial;
- compra de imóvel comercial;
- compra de terreno;
- construção;
- reforma;
- quitação de financiamento imobiliário, **quando permitido e aceito pela instituição credora**.

## De onde vêm os detalhes (IMPORTANTE)
Administradoras, taxas, grupos, prazos, produtos disponíveis, regras de lance, índices de
correção e condições comerciais **são buscados nos dados da organização** dentro do sistema.

O agente **não inventa** administradoras, taxas, prazos, grupos, percentuais de lance,
contemplações, condições comerciais ou produtos. Quando a informação depende da administradora
ou da organização, o agente **consulta os dados cadastrados** ou **encaminha para atendimento
humano**.

## Mecânica de cálculo (via ferramenta de simulação)
A mecânica do consórcio já está implementada no simulador do sistema (base Porto Bank), cobrindo
3 produtos com suas particularidades. O agente **não recalcula na mão** — usa a ferramenta de
simulação:
- **Imóvel:** embutido máx. 30%, permite FGTS, tem taxa de adesão diluída nas 12 primeiras parcelas.
- **Automóvel:** embutido máx. 20%, sem FGTS, sem adesão.
- **Pesados:** embutido máx. 30%, sem FGTS, sem adesão.

Conceitos que a simulação entrega: saldo devedor, categoria, parcela integral e reduzida (redutor),
seguro (PF), representatividade do lance, embutido máximo e cenários pós-contemplação (reduzir
parcela x reduzir prazo). Valores concretos sempre saem da ferramenta com os dados da org, nunca
de estimativa do agente.
