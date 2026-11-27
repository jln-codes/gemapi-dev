# GEMAPI-INIT-03 — Identité visuelle

Les actifs distribués sous `/branding/` sont des copies binaires des
ressources approuvées. La copie sert à inclure les actifs dans la wheel
frontale, qui reste installable seule à partir de `webapp/`. Les fichiers
graphiques et le manifeste d’origine restent inchangés dans `branding/`.

L’entête commun charge le cartouche avec sa proportion 3:1. La Base affiche
son état d’accès fermé dans la même interface. Les pages déclarent le favicon,
l’icône Apple et le manifeste à la racine d’origine. Le manifeste publié
conserve son point d’entrée et son scope `/`. La racine redirige vers Carte.
Les actifs de marque sont inclus dans le calcul de version et dans le cache
du shell. Les anciennes icônes Digues ne sont plus distribuées.

Vérifications prévues en 04 depuis la wheel et le proxy. Comparaison octet
pour octet, dimensions et usages PWA, affichage étroit du cartouche et
réouverture hors connexion. L’installation native dépend de la recette
matérielle.
