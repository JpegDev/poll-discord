# poll-discord

Sondage Discord avec boutons interactifs et rappels automatiques.

## Fonctionnalités

- 📊 Sondages classiques avec choix multiples
- ✅ Sondages de présence (Présent/En attente/Absent)
- ⏰ Rappels automatiques aux non-votants
- 🔔 Rappels pour participants "En attente" (J-2, J-1)
- 📅 Dates d'événement et dates limites

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Variables d'environnement :
- `DATABASE_URL` - URL de la base de données PostgreSQL
- `TOKEN_DISCORD` - Token du bot Discord

## Commandes

- `/poll` - Créer un sondage
  - Laissez les choix vides pour un sondage de présence
  - Utilisez `single: true` pour un choix unique
- `/check_polls` - Vérifier l'état des sondages (admin)

## Tests

```bash
python -m pytest tests/ -v
```

## Licence

MIT