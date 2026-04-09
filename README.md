# poll-discord

[![Build & Test](https://github.com/JpegDev/poll-discord/actions/workflows/build.yml/badge.svg)](https://github.com/JpegDev/poll-discord/actions/workflows/build.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-30%20passed-green.svg)](#tests)

Sondage Discord avec boutons interactifs et rappels automatiques.

## Fonctionnalités

- 📊 Sondages classiques avec choix multiples
- ✅ Sondages de présence (Présent/En attente/Absent)
- ⏰ Rappels automatiques aux non-votants
- 🔔 Rappels pour participants "En attente" (J-2, J-1)
- 📅 Dates d'événement et dates limites
- ✏️ Modification des votes par les éditeurs (rôle configurable)
- 📆 Création automatique d'événement Discord pour les sondages de présence
  - Le nom du channel est inclus dans le nom de l'événement
- 🗑️ Suppression de l'événement lors de la suppression du sondage

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Variables d'environnement :
- `DATABASE_URL` - URL de la base de données PostgreSQL
- `TOKEN_DISCORD` - Token du bot Discord
- `EDITOR_ROLE_ID` - ID du rôle éditeur de sondage (pour modifier les votes)

## Commandes

- `/poll` - Créer un sondage
  - Laissez les choix vides pour un sondage de présence
  - Utilisez `single: true` pour un choix unique
- `/check_polls` - Vérifier l'état des sondages (admin)
- `/delete_poll` - Supprimer un sondage et son événement (rôle éditeur)
- `/clean_events` - Supprimer les événements orphanés (rôle éditeur)

## Tests

```bash
python -m pytest tests/ -v
```

## Licence

MIT