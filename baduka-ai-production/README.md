# BADUKA AI — base de produção

Este projecto acrescenta uma API real ao protótipo estático. A base usa FastAPI, PostgreSQL, SQLAlchemy/Alembic, autenticação com hash de palavra-passe e JWT, chamadas server-side à OpenAI Responses API e Stripe Checkout/Webhooks.

## Estado

- Registo, verificação de email, login, refresh, logout e consulta de perfil: implementados no backend.
- O email configurado em `OWNER_EMAIL` recebe o papel de administrador e acesso total depois de verificar a conta.
- Outras contas começam sem acesso ao portal; o administrador pode aprovar ou suspender cada conta.
- Free: 100 créditos de utilização única no primeiro registo. Pro: 600/mês. Business: 9 000/mês. Créditos pagos adicionais não expiram.
- O consumo de créditos e o estado da subscrição são alterados no servidor. O cliente nunca define preço, papel ou saldo.
- Checkout para Pro/Business, pacotes de créditos e portal de facturação usam Price IDs do servidor. Webhooks Stripe validam assinatura e deduplicam eventos.
- A API de IA usa a Responses API da OpenAI, com `store=false`, limite de saída e chave exclusivamente no servidor.
- A chave da OpenAI e os segredos Stripe ficam no servidor, em variáveis de ambiente.

## Arranque local

1. Copie `.env.example` para `.env` e defina segredos próprios. Gere `JWT_SECRET` com pelo menos 32 bytes aleatórios e altere `POSTGRES_PASSWORD`.
2. Configure `OWNER_EMAIL`, SMTP, chave da OpenAI e Price IDs do Stripe em modo de teste.
3. Execute `docker compose up --build`.
4. A API ficará em `http://localhost:8000`; documentação interactiva em `/docs`.
5. Registe o administrador com o email de `OWNER_EMAIL`, confirme a mensagem de verificação e entre. Em desenvolvimento, a ligação de verificação aparece nos logs da API.

O modo local regista no terminal a ligação de verificação quando `APP_ENV=development`. Em produção, configure SMTP antes de aceitar registos.

## Planos e créditos

Configure no Stripe os produtos/preços recorrentes Pro e Business e os preços avulsos dos pacotes. Copie os Price IDs para o ambiente. O servidor selecciona os Price IDs por chave fixa e só concede créditos ao receber eventos Stripe assinados e idempotentes.

Eventos necessários no endpoint `/api/billing/webhook`:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `invoice.paid`
- `invoice.payment_failed`
- `customer.subscription.updated`
- `customer.subscription.deleted`

Em produção, use HTTPS, PostgreSQL gerido com backups, SMTP verificado, chaves live do Stripe, uma chave OpenAI com limites de gasto e um domínio da BADUKA AI. Nunca publique `.env`.

## API principal

- `POST /api/auth/register`, `/verify-email`, `/login`, `/refresh`, `/logout`, `GET /api/auth/me`
- `GET /api/admin/users`, `PATCH /api/admin/users/{id}` e `GET /api/admin/overview`
- `POST /api/ai/respond`
- `POST /api/billing/checkout`, `/credits/checkout`, `/portal`, `/webhook`
- `GET /health/live`, `GET /health/ready`

O HTML que acompanha o protótipo ainda não está ligado a esta API. A integração do frontend deve usar a origem da API configurada, manter o access token em memória e usar o cookie HttpOnly de refresh. OAuth Google/GitHub/Apple requer client IDs e callbacks próprios e permanece fora deste primeiro corte.

## Limites conhecidos antes do lançamento

Este é um backend inicial preparado para integração, não uma publicação comercial concluída. O HTML que acompanha o protótipo ainda não está ligado a esta API. Faltam credenciais reais, domínio/HTTPS, SMTP, produtos e Price IDs Stripe, configuração do webhook, OAuth social, políticas de privacidade/termos, revisão independente de segurança, monitorização, backups geridos e testes de ponta a ponta. O serviço de IA nesta versão responde a chat; ainda não tem ferramentas de execução de código/sandbox. Não coloque dados reais de clientes até completar esses itens.
