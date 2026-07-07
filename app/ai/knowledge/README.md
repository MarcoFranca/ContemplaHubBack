# Base de Conhecimento do Agente (camada GLOBAL)

Esta pasta é a **camada global** do agente de IA: conhecimento e regras que valem para
**todas as organizações** e que **somente o super admin da plataforma** pode editar.

Princípios:
- **Imutável para as orgs.** Nenhum usuário de org altera estes arquivos nem os guardrails.
- **Não inventar.** Administradoras, taxas, grupos, prazos, produtos disponíveis, regras de
  lance e condições comerciais **vêm dos dados da organização** (via ferramentas), nunca daqui.
- **Consultivo, não vendedor agressivo.** Orientar com base em objetivo, prazo, renda, reserva
  e regras do grupo/administradora.
- **Sem promessas.** Contemplação ocorre por sorteio ou lance; nunca prometer prazo/resultado.

Arquivos:
- `01_negocio.md` — quem somos, proposta de valor, diferenciais
- `02_produtos.md` — modalidades vendidas (especificidades vêm do sistema)
- `03_publico.md` — perfis de cliente e objetivos
- `04_objecoes.md` — objeções comuns e como responder (Bloco 2)
- `05_faq.md` — perguntas frequentes (Bloco 2)
- `06_qualificacao.md` — perguntas de qualificação (Bloco 3)
- `07_processo_venda.md` — etapas e quando escalar (Bloco 3)
- `08_compliance.md` — o que PODE e NÃO PODE dizer (BACEN/LGPD)
- `09_tom_persona.md` — tom de voz e persona

> Especificidades de cada org (administradoras, taxas, produtos, grupos) NÃO ficam aqui —
> são lidas dos dados da org, isoladas por `org_id`.
