FROM python:3.11-slim

# Crée un dossier de travail
WORKDIR /app

# Copie les fichiers du projet
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Exécute le bot
CMD ["python", "bot.py"]