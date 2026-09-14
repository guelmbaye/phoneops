# Guide de déploiement — PHONEOPS AI sur DigitalOcean

## Autonomous Exception Recovery · Next.js + FastAPI + PostgreSQL + Redis

> **Domaines cibles**
> - `phoneops.vylantic.com` — Mission Control (Next.js)
> - `ai.phoneops.vylantic.com` — API de récupération (FastAPI)
>
> Ce guide installe PHONEOPS sur un droplet DigitalOcean avec une **infrastructure
> mutualisable** : reverse proxy Nginx partagé, réseau Docker dédié. Si le droplet
> héberge déjà une autre application, PHONEOPS s'ajoute sans conflit.

---

## Architecture cible

| Service | Domaine | Technologie | Port interne |
|---|---|---|---|
| Mission Control | `phoneops.vylantic.com` | Next.js 15 (App Router) | 3000 |
| API | `ai.phoneops.vylantic.com` | FastAPI + Python 3.11 | 8000 |
| Base de données | (interne) | PostgreSQL 16 | 5432 |
| Bus d'événements | (interne) | Redis 7 | 6379 |

Quatre services, pas davantage : PHONEOPS n'utilise ni Elasticsearch, ni stockage
objet, ni worker de queue, ni scheduler. La boucle de récupération s'exécute dans
le processus API, et le bus d'événements passe par Redis en pub/sub.

### Deux points d'architecture à comprendre avant de commencer

**Le navigateur ne parle jamais à l'API.** `next.config.ts` relaie `/api/*` vers
`API_ORIGIN` côté serveur. Tout le trafic du navigateur va donc sur
`phoneops.vylantic.com` — pas de CORS, pas d'hôte d'API exposé au client.

**Alors pourquoi un domaine public pour l'API ?** Pour une seule raison :
**CALL-E doit pouvoir rappeler**. Le webhook de résultat terminal est une requête
entrante depuis l'infrastructure CALL-E vers `CALLE_WEBHOOK_URL`. Sans domaine
public sur l'API, PHONEOPS retombe sur le polling — fonctionnel, mais plus lent et
plus bavard.

Si vous ne prévoyez pas d'appels réels, `ai.phoneops.vylantic.com` est facultatif :
gardez l'API uniquement sur le réseau Docker interne.

---

# 1. Architecture globale

```text
Internet
   │
   ▼
Cloudflare (optionnel — DNS + CDN + WAF)
   │                                    ▲
   ▼                                    │ webhook CALL-E
┌────────────────────────────────────────────────────────────┐
│  Droplet Ubuntu 24.04 LTS  (2 vCPU / 4 Go RAM / 50 Go SSD) │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  Réseau Docker partagé : proxy-network               │  │
│  │  proxy-nginx — ports 80/443, TLS Let's Encrypt       │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  ┌────────────── PHONEOPS ──────────────────────────────┐  │
│  │  Réseau : phoneops-network                           │  │
│  │   • phoneops-web       :3000  (Next.js standalone)   │  │
│  │   • phoneops-api       :8000  (FastAPI + SSE)        │  │
│  │   • phoneops-postgres  :5432                         │  │
│  │   • phoneops-redis     :6379                         │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

Un droplet 2 vCPU / 4 Go suffit : la charge est dominée par l'attente d'appels
téléphoniques, pas par le calcul.

---

# 2. Prérequis droplet

Si le droplet existe déjà, passer à la section 5.

- **Plan** : 2 vCPU / 4 Go RAM / 50 Go SSD NVMe
- **OS** : Ubuntu 24.04 LTS
- **Région** : Frankfurt (`fra1`) ou Amsterdam (`ams3`)
- **Backups** : activés

---

# 3. Hardening (si nouveau droplet)

```bash
apt update && apt upgrade -y
apt install -y curl wget vim git ufw fail2ban unzip ca-certificates gnupg \
  lsb-release htop jq tree net-tools dnsutils

adduser deploy && usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh

sed -i 's/^PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart ssh

timedatectl set-timezone Africa/Casablanca
systemctl enable --now fail2ban
```

> Le fuseau du serveur n'a **aucune influence** sur la logique métier : PHONEOPS
> lit `MISSION_TIMEZONE` (§9) et stocke tous les instants en UTC. Régler
> l'horloge système reste utile pour les logs et les sauvegardes.

---

# 4. Firewall + Docker (si nouveau droplet)

```bash
ufw default deny incoming && ufw default allow outgoing
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
usermod -aG docker deploy
```

> Docker Compose **v2.24 ou plus récent** est requis : le `docker-compose.yml` du
> dépôt utilise la syntaxe longue `env_file: [{path, required}]`.
> Vérifier avec `docker compose version`.

---

# 5. DNS

Deux enregistrements A vers l'IP du droplet :

| Type | Nom | Valeur |
|---|---|---|
| A | `phoneops` | `<IP_DROPLET>` |
| A | `ai.phoneops` | `<IP_DROPLET>` |

```bash
dig +short phoneops.vylantic.com
dig +short ai.phoneops.vylantic.com
```

Attendre que les deux répondent avant d'émettre les certificats (§13).

---

# 6. Structure serveur

```bash
mkdir -p /var/www/phoneops
chown -R deploy:deploy /var/www/phoneops
```

Le dépôt est un monorepo : `apps/api/`, `apps/web/`, `docker-compose.yml`.

---

# 7. Réseaux Docker

```bash
# Réseau proxy (existe déjà si une autre app est installée)
docker network create proxy-network 2>/dev/null || true

# Réseau dédié PHONEOPS
docker network create phoneops-network
```

---

# 8. Reverse proxy Nginx

> Si le proxy est déjà en place, il suffit d'ajouter les deux vhosts ci-dessous
> dans `/var/www/proxy/nginx/conf.d/`.

## ⚠ Le point le plus important de ce guide

Mission Control reçoit l'état de la mission par **Server-Sent Events**. Deux
réglages Nginx cassent le flux silencieusement :

- **la compression** — un encodeur gzip bufferise, et le flux n'arrive jamais ;
- **le buffering proxy** — Nginx accumule la réponse avant de la transmettre.

Le symptôme est traître : le flux fonctionne parfaitement sous `curl` (qui
n'annonce pas `Accept-Encoding` par défaut) et ne délivre **rien** à un
navigateur, qui l'annonce toujours. L'interface reste figée sur l'état initial
sans la moindre erreur.

L'application envoie déjà `X-Accel-Buffering: no` et `Content-Encoding: identity`,
mais il faut le confirmer côté proxy — un `gzip on;` global du fichier
`nginx.conf` reprendrait le dessus.

### `/var/www/proxy/nginx/conf.d/phoneops-web.conf`

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name phoneops.vylantic.com;

    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name phoneops.vylantic.com;

    ssl_certificate     /etc/letsencrypt/live/phoneops.vylantic.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/phoneops.vylantic.com/privkey.pem;

    include /etc/nginx/snippets/ssl.conf;
    include /etc/nginx/snippets/security.conf;

    access_log /var/log/nginx/phoneops-web-access.log main;
    error_log  /var/log/nginx/phoneops-web-error.log warn;

    # Flux d'événements de mission. Cet emplacement doit précéder `location /`.
    location ~ ^/api/recovery-missions/[^/]+/stream$ {
        proxy_pass http://phoneops-web:3000;
        include /etc/nginx/snippets/proxy.conf;

        gzip                off;      # sinon le flux est bufferisé par l'encodeur
        proxy_buffering     off;
        proxy_cache         off;
        proxy_set_header    Accept-Encoding "";
        proxy_http_version  1.1;
        proxy_set_header    Connection "";
        proxy_read_timeout  3600s;    # une mission peut rester ouverte longtemps
        chunked_transfer_encoding on;
    }

    location /_next/static/ {
        proxy_pass http://phoneops-web:3000;
        include /etc/nginx/snippets/proxy.conf;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location / {
        proxy_pass http://phoneops-web:3000;
        include /etc/nginx/snippets/proxy.conf;
    }
}
```

### `/var/www/proxy/nginx/conf.d/phoneops-api.conf`

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name ai.phoneops.vylantic.com;

    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ai.phoneops.vylantic.com;

    ssl_certificate     /etc/letsencrypt/live/ai.phoneops.vylantic.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.phoneops.vylantic.com/privkey.pem;

    include /etc/nginx/snippets/ssl.conf;
    include /etc/nginx/snippets/security.conf;

    access_log /var/log/nginx/phoneops-api-access.log main;
    error_log  /var/log/nginx/phoneops-api-error.log warn;

    client_max_body_size 2m;   # les payloads CALL-E sont petits

    # Même traitement pour le flux servi directement par l'API.
    location ~ ^/api/recovery-missions/[^/]+/stream$ {
        proxy_pass http://phoneops-api:8000;
        include /etc/nginx/snippets/proxy.conf;

        gzip                off;
        proxy_buffering     off;
        proxy_cache         off;
        proxy_set_header    Accept-Encoding "";
        proxy_http_version  1.1;
        proxy_set_header    Connection "";
        proxy_read_timeout  3600s;
    }

    location / {
        proxy_pass http://phoneops-api:8000;
        include /etc/nginx/snippets/proxy.conf;
    }
}
```

---

# 9. Variables d'environnement

### `/var/www/phoneops/.env`

Partir de `.env.example` du dépôt, qui documente chaque clé.

```bash
# ─── Mission Control (apps/web) ──────────────────────
# Relais côté serveur. Le navigateur ne voit jamais cette valeur.
API_ORIGIN=http://phoneops-api:8000

# ─── Runtime ─────────────────────────────────────────
APP_ENV=production
LOG_LEVEL=INFO
WEB_ORIGINS=https://phoneops.vylantic.com

# ─── Stockage ────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://phoneops:CHANGE_ME@phoneops-postgres:5432/phoneops
REDIS_URL=redis://phoneops-redis:6379/0
AUTO_CREATE_SCHEMA=false        # Alembic fait foi en production

# ─── Fuseau d'exploitation ───────────────────────────
# Le fuseau auquel appartient une heure murale comme « 17:30 ».
# C'est l'horloge de l'entrepôt, pas celle du serveur.
MISSION_TIMEZONE=Africa/Casablanca

# ─── CALL-E ──────────────────────────────────────────
CALLE_MODE=http
CALLE_BASE_URL=https://...                      # fourni à l'onboarding
CALLE_API_KEY=sk-...
CALLE_WEBHOOK_URL=https://ai.phoneops.vylantic.com/api/webhooks/calle
CALLE_WEBHOOK_SECRET=$(openssl rand -hex 32)
CALLE_DEFAULT_REGION=MA
CALLE_DEFAULT_LOCALE=fr-MA

# ─── LLM (optionnel) ─────────────────────────────────
LLM_ENABLED=false
LLM_API_KEY=
LLM_MODEL=claude-sonnet-4-6

# ─── Garde-fous ──────────────────────────────────────
MAX_REPLANS=3
MAX_CALL_RETRIES=2
MAX_OPERATIONS_PER_MISSION=8
APPROVAL_COST_INCREASE_PCT=15
EVIDENCE_TTL_MINUTES=120
MIN_RECOVERY_WINDOW_SECONDS=120

# ─── Démo ────────────────────────────────────────────
DEMO_MODE=true
DEMO_CARRIER_B_PICKUP=18:00
DEMO_CALL_LATENCY_SECONDS=1.5
DEMO_CARRIER_B_PHONE=+212...
DEMO_CARRIER_C_PHONE=+212...
DEMO_CARRIER_D_PHONE=+212...
```

```bash
chmod 600 /var/www/phoneops/.env
```

## ⚠ Les endpoints de démo déclenchent de vrais appels

`POST /api/demo/flagship` est **public et non authentifié**. Avec
`CALLE_MODE=http`, chaque visiteur qui l'appelle fait sonner les numéros de
`DEMO_CARRIER_*` et consomme vos crédits.

Trois options, par ordre de prudence :

1. **`CALLE_MODE=mock`** sur le déploiement public — la boucle complète tourne,
   chaque appel est étiqueté « Simulated CALL-E provider » à l'écran, et personne
   n'est dérangé. C'est le bon choix pour une URL destinée à un jury.
2. **`DEMO_MODE=false`** — désactive le scénario préchargé.
3. **Restreindre `/api/demo/` dans Nginx** à vos adresses IP :

```nginx
location /api/demo/ {
    allow 203.0.113.0/24;   # votre IP
    deny all;
    proxy_pass http://phoneops-api:8000;
    include /etc/nginx/snippets/proxy.conf;
}
```

Utilisez des numéros que vous contrôlez pour `DEMO_CARRIER_*`, jamais ceux de
transporteurs réels.

> En `CALLE_MODE=http`, les personas scriptés **ne s'appliquent plus** : ce sont
> de vrais interlocuteurs qui répondent. Avec les numéros fictifs `+1555…` du
> gabarit, aucun appel n'aboutit et la mission escalade après deux tentatives par
> transporteur — comportement correct, mais ce n'est pas le scénario flagship.

---

# 10. Docker Compose production

Le `docker-compose.yml` du dépôt est prévu pour le **développement local** :
il publie les ports 5432 et 6379 sur l'hôte et code en dur les identifiants
PostgreSQL. Ne pas l'utiliser tel quel en production.

### `/var/www/phoneops/docker-compose.prod.yml`

```yaml
name: phoneops

services:
  postgres:
    image: postgres:16-alpine
    container_name: phoneops-postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: phoneops
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: phoneops
    volumes: ["pgdata:/var/lib/postgresql/data"]
    networks: [phoneops-network]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U phoneops"]
      interval: 10s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    container_name: phoneops-redis
    restart: unless-stopped
    command: redis-server --save 60 1 --loglevel warning
    volumes: ["redisdata:/data"]
    networks: [phoneops-network]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 10

  api:
    build: ./apps/api
    container_name: phoneops-api
    restart: unless-stopped
    env_file: [.env]
    # Une seule ligne, délibérément. Un scalaire plié (`>`) conserve le saut de
    # ligne d'une ligne plus indentée : `sh -c` reçoit alors trois commandes et
    # meurt sur `--forwarded-allow-ips=*: not found`.
    command: sh -c "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"
    networks: [phoneops-network, proxy-network]
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}

  web:
    build: ./apps/web
    container_name: phoneops-web
    restart: unless-stopped
    environment:
      API_ORIGIN: http://phoneops-api:8000
      NODE_ENV: production
    networks: [phoneops-network, proxy-network]
    depends_on:
      api: {condition: service_healthy}

volumes:
  pgdata:
  redisdata:

networks:
  phoneops-network:
    external: true
  proxy-network:
    external: true
```

Ce qui change par rapport au fichier de développement :

| | dev | prod |
|---|---|---|
| Ports PostgreSQL / Redis | publiés sur l'hôte | **jamais publiés** |
| Ports API / web | publiés | uniquement via `proxy-network` |
| Mot de passe PostgreSQL | en dur | `${POSTGRES_PASSWORD}` |
| `restart` | absent | `unless-stopped` |
| `--proxy-headers` | absent | requis derrière Nginx |
| Persistance Redis | aucune | volume + `--save` |

`depends_on: service_healthy` fonctionne sans déclarer de `healthcheck` dans le
compose : les deux `Dockerfile` en portent un (`HEALTHCHECK` sur `/api/health`
pour l'API, sur `/` pour le web).

---

# 11. Configuration Next.js

`apps/web/next.config.ts` ne contient qu'un réglage, **à ne pas modifier** :

```typescript
output: "standalone",   // requis par le Dockerfile multi-stage
```

## ⚠ Pourquoi il n'y a pas de `rewrites()`

Next **compile** les destinations de rewrite dans `routes-manifest.json` au
moment du build. Une image construite sans `API_ORIGIN` gèle donc la cible sur
`http://localhost:8000` — c'est-à-dire le conteneur web lui-même — et la
variable fournie au runtime par `docker compose` n'est jamais lue. Résultat :
toutes les requêtes `/api/*` renvoient `500` sans message.

Le relais est une **route handler** (`src/app/api/[...path]/route.ts`) qui lit
l'environnement à chaque requête. La même image tourne donc en local et en
production, seule la variable change.

Quand l'API est injoignable, elle répond `502` avec un corps qui nomme la cible :

```json
{
  "error": "recovery_engine_unreachable",
  "message": "Could not reach the recovery engine at http://phoneops-api:8000: fetch failed",
  "hint": "Check API_ORIGIN and that the API container is healthy."
}
```

La route SSE (`/api/recovery-missions/[id]/stream`) reste séparée : plus
spécifique, elle l'emporte sur ce catch-all, et elle a son propre traitement
pour ne pas être bufferisée.

---

# 12. Récupération du code

```bash
cd /var/www/phoneops
git clone <URL_DEPOT> .
cp .env.example .env     # puis renseigner selon §9
chmod 600 .env
```

---

# 13. Certificats Let's Encrypt

### 13.1 — Bootstrap HTTP

Commenter temporairement les blocs `server { listen 443 ... }` des deux vhosts,
puis :

```bash
docker exec proxy-nginx nginx -t && docker exec proxy-nginx nginx -s reload
```

### 13.2 — Émettre

```bash
docker run --rm \
  -v /var/www/proxy/certbot/conf:/etc/letsencrypt \
  -v /var/www/proxy/certbot/www:/var/www/certbot \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d phoneops.vylantic.com \
  -d ai.phoneops.vylantic.com \
  --email ops@vylantic.com --agree-tos --no-eff-email
```

### 13.3 — Réactiver et recharger

```bash
docker exec proxy-nginx nginx -t && docker exec proxy-nginx nginx -s reload
```

### 13.4 — Vérifier

```bash
curl -I https://phoneops.vylantic.com
curl -s https://ai.phoneops.vylantic.com/api/health | jq
```

---

# 14. Premier déploiement

```bash
cd /var/www/phoneops
export POSTGRES_PASSWORD=$(grep -oP '(?<=phoneops:)[^@]+(?=@)' .env)

docker compose -f docker-compose.prod.yml config >/dev/null   # valider
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f api
```

Les migrations Alembic tournent dans la commande du service `api` — aucune étape
manuelle. Vérifier qu'elles ont abouti :

```bash
docker exec phoneops-api alembic current
```

---

# 15. Vérifications

> **Avant tout**, vérifier que le web parle bien à l'API — c'est le point de
> panne le plus probable après un premier déploiement :
>
> ```bash
> docker exec phoneops-web printenv API_ORIGIN     # doit valoir http://phoneops-api:8000
> curl -s https://phoneops.vylantic.com/api/health | jq
> ```
>
> Un `502 recovery_engine_unreachable` nomme l'origine tentée : si elle dit
> `localhost:8000`, la variable n'est pas passée au conteneur.

```bash
# 1. Santé de l'API, et mode CALL-E effectif
curl -s https://ai.phoneops.vylantic.com/api/health | jq
# → {"status":"ok","calle_mode":"http","calle_configured":true,...}

# 2. Mission Control répond
curl -sI https://phoneops.vylantic.com | head -1

# 3. Le relais interne fonctionne
curl -s https://phoneops.vylantic.com/api/health | jq .status
```

### Le test qui compte : le flux SSE sous compression

C'est celui qui attrape la panne décrite au §8, et **`curl` seul ne suffit pas** —
il faut annoncer gzip comme le fait un navigateur.

```bash
MID=$(curl -s -X POST "https://ai.phoneops.vylantic.com/api/demo/flagship?autostart=true" \
      | jq -r .id)
sleep 8

echo "sans gzip :"
curl -sN --max-time 5 \
  "https://phoneops.vylantic.com/api/recovery-missions/$MID/stream" \
  | grep -c "^event:"

echo "avec gzip (comme un navigateur) :"
curl -sN --max-time 5 -H "Accept-Encoding: gzip" \
  "https://phoneops.vylantic.com/api/recovery-missions/$MID/stream" \
  | grep -ac "^event:"
```

**Les deux doivent renvoyer le même nombre d'événements** (une trentaine pour le
scénario flagship). Si la seconde commande renvoie `0`, la compression est
réactivée quelque part — vérifier un `gzip on;` global dans `nginx.conf`, ou un
proxy intermédiaire comme Cloudflare.

> Sur Cloudflare, désactiver **Auto Minify** et **Brotli** pour l'hôte, ou créer
> une Page Rule qui exclut `/api/recovery-missions/*/stream` de la compression.

### Le parcours complet dans un navigateur

Ouvrir `https://phoneops.vylantic.com`, cliquer « Start recovery », et vérifier
que la séquence se déroule en direct : `RECOVERING` → `⚠ RECOVERY PLAN
INVALIDATED` → `REPLANNING` → `RECOVERED`. Si l'écran reste figé sur `ASSESSING`,
le problème est le flux SSE, pas la boucle.

---

# 16. Backups automatiques

Une seule base à sauvegarder : PostgreSQL. Redis ne contient que du pub/sub
transitoire, et il n'y a pas de stockage objet.

### `/usr/local/bin/backup-phoneops.sh`

```bash
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST=/var/backups/phoneops
mkdir -p "$DEST"

docker exec phoneops-postgres pg_dump -U phoneops phoneops \
  | gzip > "$DEST/db-$STAMP.sql.gz"

# Le .env contient la clé CALL-E et le secret webhook : chiffrer ou exclure.
install -m 600 /var/www/phoneops/.env "$DEST/env-$STAMP"

find "$DEST" -type f -mtime +7 -delete
```

```bash
chmod +x /usr/local/bin/backup-phoneops.sh
crontab -e
# 0 3 * * * /usr/local/bin/backup-phoneops.sh >> /var/log/backup-phoneops.log 2>&1
```

Tester la **restauration**, pas seulement la sauvegarde :

```bash
gunzip -c /var/backups/phoneops/db-XXXX.sql.gz | \
  docker exec -i phoneops-postgres psql -U phoneops phoneops
```

---

# 17. Déploiement continu

```bash
cd /var/www/phoneops
git pull
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f --tail=50 api
```

Les migrations s'appliquent au démarrage du conteneur API. En cas de migration
lourde, sauvegarder d'abord (§16).

---

# 18. Exploitation

| Besoin | Commande |
|---|---|
| Logs API | `docker logs -f phoneops-api` |
| Logs web | `docker logs -f phoneops-web` |
| Console PostgreSQL | `docker exec -it phoneops-postgres psql -U phoneops` |
| Migration courante | `docker exec phoneops-api alembic current` |
| Mode CALL-E effectif | `curl -s https://ai.phoneops.vylantic.com/api/health \| jq .calle_mode` |
| Réinitialiser la démo | `curl -X POST ".../api/demo/flagship?reset=true"` |

### Pannes fréquentes

| Symptôme | Cause la plus probable |
|---|---|
| L'écran reste sur `ASSESSING` | Compression ou buffering sur le flux SSE (§8, §15) |
| `--forwarded-allow-ips=*: not found` | `command:` écrit en scalaire plié sur plusieurs lignes — le garder sur une seule |
| `ModuleNotFoundError` au démarrage de l'API | Dépendance importée mais absente de `requirements.txt`. `make test` l'attrape désormais |
| `500` sur `/api/*`, sans corps | Version antérieure : rewrite gelé au build. Vérifier que `next.config.ts` ne déclare plus de `rewrites()` |
| `502 recovery_engine_unreachable` | Le message nomme la cible tentée. `API_ORIGIN` absent du conteneur web, ou API non démarrée |
| `502` sur les deux domaines | L'API n'est pas encore `healthy` — voir `docker logs phoneops-api` |
| `calle_configured: false` | `CALLE_API_KEY` absente du `.env` monté dans le conteneur |
| Résultats d'appel jamais reçus | `CALLE_WEBHOOK_URL` injoignable depuis Internet, ou `CALLE_WEBHOOK_SECRET` divergent |
| Cutoff affiché « Tomorrow 17:30 » | Normal après 16:45 heure de mission — le scénario de démo bascule au jour suivant |
| Missions en escalade immédiate | Fenêtre plus courte que `MIN_RECOVERY_WINDOW_SECONDS` — comportement attendu |

---

# 19. Ce qui n'a pas été vérifié

Par honnêteté : les deux `Dockerfile` et le `docker-compose.yml` du dépôt ont été
écrits et relus, mais **jamais construits** — aucun démon Docker n'était
disponible dans l'environnement de développement. Ce qui a été vérifié :

- le YAML est valide et déclare bien quatre services cohérents ;
- `apps/web` produit bien `.next/standalone/server.js`, la cible du `CMD` ;
- les deux images déclarent un `HEALTHCHECK`, ce dont dépend `service_healthy`.

Le premier `docker compose up --build` reste donc à faire avant de compter sur ce
déploiement pour une démonstration.
