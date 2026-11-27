"""Limites techniques communes du contrat d'ingestion des médias."""


# Garde-fou serveur absolu, indépendant de la politique fonctionnelle définie
# par le superadmin pour la préparation des nouvelles photos.
ABSOLUTE_MEDIA_LIMIT_BYTES = 20 * 1024 * 1024
