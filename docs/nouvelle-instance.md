# Mettre en place une nouvelle instance pour un autre serveur Discord

Chaque serveur a **sa propre instance** : son bot Discord (donc son token), sa base PostgreSQL et son rôle éditeur. Rien n'est partagé avec l'instance existante.

Vous aurez besoin de : un compte Discord avec le droit « Gérer le serveur » sur le nouveau serveur, et une machine avec Docker (et Git).

## 1. Créer le bot sur le portail développeur Discord

1. Aller sur <https://discord.com/developers/applications> → **New Application** → nommer l'application (c'est le nom du bot) → **Create**.
2. Onglet **Bot** :
   - **Reset Token** puis **Copy** : c'est le `TOKEN_DISCORD` de la nouvelle instance. Il n'est affiché qu'une fois et ne doit jamais être commité. S'il fuite, refaire **Reset Token**.
   - **Privileged Gateway Intents** : activer **Server Members Intent** (nécessaire pour les listes de votants / non-votants). Les autres intents restent désactivés.
3. Onglet **General Information** : copier l'**Application ID** (`CLIENT_ID` ci-dessous).

## 2. Inviter le bot sur le serveur

Ouvrir cette URL en remplaçant `CLIENT_ID`, choisir le serveur, **Autoriser** :

```
https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=8590003200
```

Les permissions demandées (`8590003200`) : View Channels, Send Messages, Read Message History, Manage Events (création et suppression de l'événement Discord des sondages de présence).

> Pour un salon privé, donnez aussi au bot (ou à son rôle) l'accès à ce salon.

## 3. Récupérer l'identifiant du rôle éditeur

1. Discord → **Paramètres utilisateur → Avancé → Mode développeur** : activer.
2. **Paramètres du serveur → Rôles** → clic droit sur le rôle qui pourra modifier les votes, supprimer un sondage et nettoyer les événements → **Copier l'identifiant du rôle**.

## 4. Installer l'instance

Une instance = **un dossier à part**, pour que les conteneurs et la base ne se mélangent pas avec l'existante.

```bash
git clone https://github.com/JpegDev/poll-discord.git poll-discord-NOMSERVEUR
cd poll-discord-NOMSERVEUR
```

Éditer `docker-compose.yml` dans ce nouveau dossier. Quatre choses à changer :

```yaml
services:
  discord-bot:
    container_name: discord-bot-NOMSERVEUR          # 1. nom unique
    environment:
      - TOKEN_DISCORD=le_token_du_nouveau_bot       # 2. token de l'étape 1
      - DATABASE_URL=postgresql://user:password@db:5432/sondages
      - EDITOR_ROLE_ID=123456789012345678           # 3. à AJOUTER : rôle de l'étape 3
  db:
    container_name: discord_postgres-NOMSERVEUR     # 1. nom unique
    ports:
      - "127.0.0.1:5433:5432"                       # 4. port hôte différent (ou supprimer le bloc ports)
```

- Gardez `DATABASE_URL` tel quel : le bot joint la base par le réseau interne de Docker (`db`), pas par le port publié.
- Changez le mot de passe `password` (dans `DATABASE_URL` **et** `POSTGRES_PASSWORD`) si la machine est accessible par d'autres.
- Le volume `./postgres_data` est relatif au dossier : la base de cette instance est donc indépendante.

Lancer :

```bash
docker compose up -d --build
docker compose logs -f discord-bot
```

Le log doit afficher « Base de données initialisée » puis « Bot connecté ». Les tables sont créées automatiquement au premier démarrage.

## 5. Vérifier

1. Dans le serveur, taper `/poll` : la commande apparaît (comptez quelques minutes la première fois).
2. Créer un sondage de test (`/poll` sans choix = présent / en attente / absent).
3. Avec un compte qui a le rôle éditeur, le bouton **✏️ Modifier un vote** est présent.

## Exploitation

| Action | Commande (dans le dossier de l'instance) |
|---|---|
| Voir les logs | `docker compose logs -f discord-bot` |
| Mettre à jour | `git pull && docker compose up -d --build` |
| Arrêter | `docker compose down` (la base est conservée) |

Pensez à mettre à jour **chaque instance** séparément quand le code évolue. Attention : `git pull` peut signaler un conflit si vous avez modifié `docker-compose.yml` ; dans ce cas, `git stash`, `git pull`, `git stash pop`.

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Le bot s'arrête au démarrage avec `PrivilegedIntentsRequired` | **Server Members Intent** non activé (étape 1) |
| `/poll` n'apparaît pas | attendre quelques minutes ; vérifier que l'invitation contient `applications.commands` |
| « Tu n'as pas le rôle éditeur de sondage » | `EDITOR_ROLE_ID` absent ou erroné dans le compose (id du rôle **de ce serveur**) |
| Le sondage ne se met pas à jour, pas de rappels | le bot n'a pas accès au salon (View Channels / Read Message History) |
| Pas d'événement Discord créé | permission **Manage Events** manquante |
| `port is already allocated` | le port hôte de la base est déjà pris par l'autre instance : changer `5433` |
| `container name ... is already in use` | `container_name` identique à celui de l'autre instance |
| Erreur de connexion à la base au démarrage | la base n'était pas prête : relancer `docker compose up -d` |
