# WhatsApp: Seguro de Vida Azos

O agente de WhatsApp possui um fluxo de Seguro de Vida Azos separado do fluxo de Consórcio. Ele não
chama simulador, proposta, contrato, carteira ou comissões de Consórcio.

Para cotar, coleta data de nascimento, sexo, altura, peso, profissão atual, renda mensal e tabagismo.
Antes de consultar a Azos, exige confirmação explícita do cliente autorizando o uso desses dados.
Perfis parciais não são persistidos.

O agente busca a profissão no catálogo Azos, consulta coberturas elegíveis e só calcula a cotação
com coberturas e capitais escolhidos entre as opções retornadas pela Azos. A ferramenta publica a
cotação em link público próprio de Seguro.

Quando a sincronização de propostas encontra uma `proposal_url` oficial da Azos, a proposta é
associada ao lead somente por CPF único na mesma organização. Havendo WhatsApp ativo, o backend
baixa o PDF em domínio Azos, envia-o como documento e marca `pdf_sent_at`; o mesmo PDF não é
reenviado em sincronizações futuras. Proposta sem CPF, CPF ambíguo ou telefone não recebe envio.
Essa verificação também ocorre no botão existente de sincronização da carteira Azos.

Quando o cliente confirma que quer seguir, o agente cria ou atualiza o atendimento pendente em
`seguro_azos_atendimentos` com origem `whatsapp_ia`, registra atividade no CRM e faz handoff para
o corretor. A IA permanece em silêncio após o handoff e a contratação final ocorre somente no canal
autorizado da Azos.

## Coleta guiada e recomendação

O agente coleta os dados em no máximo três blocos curtos e reaproveita o que já foi informado.
Perguntas fechadas com duas ou três respostas usam botões interativos do WhatsApp; se a Cloud API
recusar o formato, o backend envia automaticamente as mesmas opções em texto. Além dos dados
obrigatórios da API, pergunta sobre autonomia profissional, filhos/dependentes, dívidas, reserva e
orçamento mensal. Não envia perfis parciais à Azos e exige consentimento explícito antes da consulta.

Conversas de Seguro Azos preservam no mínimo 40 mensagens no contexto do agente. Antes de pedir
novamente qualquer dado, o agente deve considerar o histórico e resumir o perfil coletado para o
cliente confirmar ou corrigir.

A busca de profissão normaliza acentos e variações de escrita. Quando a ocupação exata não existe
no catálogo Azos, o agente apresenta no máximo três alternativas válidas para o cliente escolher.
Ele não escolhe outro enquadramento silenciosamente, não repete o mesmo termo em loop e encaminha
ao corretor se uma descrição adicional ainda não produzir correspondência segura.
As opções carregam o ID oficial da Azos no payload interno da mensagem interativa. Quando o cliente
seleciona um botão, o backend recupera e preserva esse ID nos turnos seguintes; correspondência por
igualdade exata sempre tem prioridade sobre nomes apenas parcialmente semelhantes.

Após receber as coberturas elegíveis, `montar_recomendacao_vida_azos` dimensiona referências de
capital por continuidade de renda, compromissos financeiros e perfil familiar/profissional. Cada
capital é limitado ao mínimo, máximo e múltiplo devolvidos pela Azos. A sugestão é explicável,
ajustável e não representa aceitação, recomendação da seguradora ou garantia de indenização.
Quando Doenças Graves 30 estiver disponível para o perfil, ela é a opção padrão. Doenças Graves
13 só deve ser usada quando DG30 não estiver disponível ou quando o cliente escolher conscientemente
uma alternativa mais enxuta para reduzir o prêmio.

O follow-up identifica o produto mais recente nas mensagens e usa cadência própria para
`seguro_azos` ou `consorcio`. Fora da janela de mensagens livres, Seguro pode usar o template Meta
configurado em `FOLLOWUP_SEGURO_TEMPLATE_NAME`; sem template aprovado, não há disparo.

## Proposta detalhada

A migration `030_seguros_azos_recomendacao.sql` adiciona `recommendation` e
`recommendation_context` à cotação. A página pública mostra a lógica geral, motivo e prioridade de
cada cobertura, capital sugerido, prêmio calculado e aviso de que os valores podem subir ou descer
conforme preferência, orçamento, subscrição e teto liberado pela Azos.

Na mensagem enviada antes do link, `CAPITAL SEGURADO` identifica o valor potencial de proteção ou
indenização de cada cobertura. `PRÊMIO MENSAL` identifica exclusivamente o total pago por mês.

## Sincronização da carteira do corretor

A chave Azos configurada para a organização é uma credencial de corretor. A sincronização usa
exclusivamente `/v1/brokers/proposals`, `/v1/brokers/policies` e `/v1/brokers/commissions`.
Endpoints `/v1/platforms/*` não devem ser usados nesse fluxo, pois a Azos os rejeita quando o
`external_id` da credencial pertence a um corretor. Falhas são registradas separadamente por recurso.
Uma falha isolada não interrompe os demais recursos: por exemplo, apólices e comissões continuam
sincronizando se propostas estiverem indisponíveis. A resposta inclui `avisos`; somente a recusa dos
três recursos encerra a operação com erro.
