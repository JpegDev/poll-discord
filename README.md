# poll-discord

[![Build & Test](https://github.com/JpegDev/poll-discord/actions/workflows/build.yml/badge.svg)](https://github.com/JpegDev/poll-discord/actions/workflows/build.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Sondage Discord avec boutons interactifs et rappels automatiques.

## Fonctionnalités

- 📊 Sondages classiques avec choix multiples ou choix unique
- ✅ Sondages de présence (Présent/En attente/Absent)
- 📅 Dates d'événement et dates limites (heure de Paris)
- 🔒 Les votes sont refusés après la date limite (ou, sans date limite, au début de l'événement) ; les boutons sont retirés à la fermeture
- 👥 Les votes de ceux qui n'ont plus accès au salon (départ, rôle retiré) ne sont plus comptés ; un nouvel arrivant peut voter tant que le sondage est ouvert. Le sondage se met à jour à chaque arrivée, départ ou changement de rôle
- ⏰ Rappels automatiques aux non-votants (tous les 2 jours à 19h)
- 🔔 Rappels pour participants "En attente" (J-2, J-1, puis hebdomadaires sans date limite)
  - Aucun rappel du vendredi 18h au dimanche 18h, ni une fois l'événement passé
- ✏️ Modification des votes par les éditeurs (rôle configurable, 25 membres affichés au maximum)
- 📆 Création automatique d'événement Discord pour les sondages de présence
  - Le nom du channel est inclus dans le nom de l'événement
- 🗑️ Suppression de l'événement lors de la suppression du sondage

## Installation

```bash
pip install -r requirements.txt
```

Pour créer le bot sur Discord et le déployer (y compris pour **un autre serveur**), voir
[docs/nouvelle-instance.md](docs/nouvelle-instance.md). Chaque serveur a sa propre instance (bot, base et rôle éditeur distincts).

## Configuration

Variables d'environnement, à mettre dans un fichier `.env` (voir `.env.example`, ignoré par git et par Docker) :
- `POSTGRES_PASSWORD` - Mot de passe de la base (docker compose construit `DATABASE_URL` avec)
- `TOKEN_DISCORD` - Token du bot Discord
- `EDITOR_ROLE_ID` - ID du rôle éditeur de sondage (pour modifier les votes)

Le bot demande l'intent privilégié **Server Members** (à activer sur le portail développeur Discord).
Les tables sont créées automatiquement au démarrage.

## Commandes

Toutes les commandes sont réservées aux serveurs (inutilisables en message privé).

- `/poll` - Créer un sondage
  - Laissez les choix vides pour un sondage de présence
  - Utilisez `single: true` pour un choix unique
  - Dates au format `JJ/MM/AAAA` ou `JJ/MM/AAAA-HH:mm` ; sans heure, **23:59** est utilisée (toute la journée) ; une date limite sans heure le jour même de l'événement s'arrête à l'heure de l'événement
  - 80 caractères maximum par choix
- `/check_polls` - Vérifier l'état des sondages (admin)
- `/delete_poll` - Supprimer un sondage et son événement (rôle éditeur)
- `/clean_events` - Supprimer les événements orphanés (rôle éditeur)

## Tests

```bash
python -m pytest tests/ -v
```

## Licence

MIT
